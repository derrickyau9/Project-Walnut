#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/reboot.h>
#include <net/if.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/ioctl.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

static int data_mounted = 0;
static const char *phase;

static void finish(int passed) {
    sync();
    if (data_mounted) {
        if (umount("/newroot/data") != 0) {
            perror("guest data unmount");
            passed = 0;
        } else {
            data_mounted = 0;
            puts("HF_IMAGE_DATA_UNMOUNTED clean=1");
        }
    }
    sync();
    printf("HF_IMAGE_RESULT %s phase=%s immutable-rootfs separate-data synthetic-only\n",
           passed ? "PASS" : "FAIL", phase ? phase : "invalid");
    reboot(LINUX_REBOOT_CMD_POWER_OFF);
    for (;;) pause();
}

static void check(int success, const char *operation) {
    if (!success) { perror(operation); finish(0); }
}

static int modules(void) {
    FILE *list = fopen("/modules/load-order", "r");
    if (!list) return 0;
    char name[128];
    unsigned int count = 0;
    while (fgets(name, sizeof name, list)) {
        size_t length = strlen(name);
        if (!length || name[length - 1] != '\n' || ++count > 32) { fclose(list); return 0; }
        name[--length] = '\0';
        if (!length) { fclose(list); return 0; }
        for (size_t i = 0; i < length; ++i)
            if (!((name[i] >= 'a' && name[i] <= 'z') || (name[i] >= '0' && name[i] <= '9') ||
                  name[i] == '_' || name[i] == '-')) { fclose(list); return 0; }
        char path[160];
        if (snprintf(path, sizeof path, "/modules/%s.ko", name) >= (int)sizeof path) { fclose(list); return 0; }
        int file = open(path, O_RDONLY | O_NOFOLLOW);
        if (file < 0) { fclose(list); return 0; }
        int result = (int)syscall(SYS_finit_module, file, "", 0);
        int saved = errno;
        close(file);
        if (result != 0 && saved != EEXIST) {
            errno = saved; perror(path); fclose(list); return 0;
        }
        printf("HF_IMAGE_MODULE %s loaded=1\n", name);
    }
    int valid = !ferror(list) && count >= 1;
    fclose(list);
    return valid;
}

static void wait_device(const char *path) {
    struct stat info;
    for (int i = 0; i < 100; ++i) {
        if (stat(path, &info) == 0 && S_ISBLK(info.st_mode)) return;
        usleep(100000);
    }
    errno = ENODEV; check(0, path);
}

static void make_directory(const char *path, mode_t mode, uid_t owner) {
    if (mkdir(path, mode) != 0 && errno != EEXIST) check(0, path);
    check(chmod(path, mode) == 0, "guest chmod");
    check(chown(path, owner, owner) == 0, "guest chown");
}

static void loopback(void) {
    int file = socket(AF_INET, SOCK_DGRAM, 0);
    check(file >= 0, "guest loopback socket");
    struct ifreq request;
    memset(&request, 0, sizeof request);
    strcpy(request.ifr_name, "lo");
    struct sockaddr_in *address = (struct sockaddr_in *)&request.ifr_addr;
    address->sin_family = AF_INET;
    address->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    check(ioctl(file, SIOCSIFADDR, &request) == 0, "guest loopback address");
    address->sin_addr.s_addr = htonl(0xff000000U);
    check(ioctl(file, SIOCSIFNETMASK, &request) == 0, "guest loopback mask");
    check(ioctl(file, SIOCGIFFLAGS, &request) == 0, "guest loopback flags");
    request.ifr_flags |= IFF_UP;
    check(ioctl(file, SIOCSIFFLAGS, &request) == 0, "guest loopback up");
    close(file);
    puts("HF_IMAGE_LOOPBACK private=1 external_nic=0");
}

static int run_ui(void) {
    pid_t child = fork();
    if (child < 0) return -1;
    if (child == 0) {
        if (chroot("/newroot") != 0 || chdir("/opt/hasslefree") != 0 ||
            setgroups(0, NULL) != 0 || setgid(1000) != 0 || setuid(1000) != 0) {
            perror("guest normal-user runtime"); _exit(126);
        }
        clearenv();
        setenv("PATH", "/usr/bin:/bin", 1);
        setenv("HOME", "/data/home", 1);
        setenv("LANG", "C.UTF-8", 1);
        setenv("LC_ALL", "C.UTF-8", 1);
        setenv("PYTHONPATH", "/opt/hasslefree", 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("PYTHONNOUSERSITE", "1", 1);
        setenv("QT_QPA_PLATFORM", "offscreen", 1);
        setenv("QT_QUICK_BACKEND", "software", 1);
        setenv("QSG_RENDER_LOOP", "basic", 1);
        setenv("QML_DISABLE_DISK_CACHE", "1", 1);
        setenv("XDG_RUNTIME_DIR", "/run/user/1000", 1);
        setenv("XDG_CACHE_HOME", "/data/cache", 1);
        setenv("XDG_CONFIG_HOME", "/data/config", 1);
        setenv("XDG_DATA_HOME", "/data/userdata", 1);
        setenv("QT_QPA_FONTDIR", "/usr/share/fonts/truetype/dejavu", 1);
        setenv("QT_PLUGIN_PATH", "/usr/lib/aarch64-linux-gnu/qt5/plugins", 1);
        setenv("QML2_IMPORT_PATH", "/usr/lib/aarch64-linux-gnu/qt5/qml", 1);
        char *arguments[] = {"/usr/bin/python3", "-B", "/opt/hasslefree/tests/ui/arm64_smoke.py",
            "--native-daemon", "/opt/hasslefree/bin/hf-camerad", "--output-dir", "/data/ui-r7",
            "--execution-mode", "qemu-system", "--phase", (char *)phase, "--timeout-seconds", "180", NULL};
        printf("HF_IMAGE_RUNTIME uid=%ld phase=%s python=/usr/bin/python3\n", (long)getuid(), phase);
        execv(arguments[0], arguments);
        perror("guest ARM Python exec"); _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0) if (errno != EINTR) return -1;
    if (!WIFEXITED(status)) return -1;
    return WEXITSTATUS(status);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    const char *marker = getenv("HASSLEFREE_ARM64_IMAGE");
    phase = getenv("HF_IMAGE_PHASE");
    struct utsname machine;
    if (getpid() != 1 || !marker || strcmp(marker, "1") != 0 || !phase ||
        (strcmp(phase, "first") != 0 && strcmp(phase, "resume") != 0) ||
        uname(&machine) != 0 || strcmp(machine.machine, "aarch64") != 0) {
        fputs("HF_IMAGE_INIT guest-only: ARM64 PID 1 and explicit image marker/phase required\n", stderr);
        return 2; /* No host/user-mode mounts, module loads, chroot, or reboot. */
    }
    printf("HF_IMAGE_INIT pid=1 machine=%s phase=%s kernel=%s\n", machine.machine, phase, machine.release);
    check(mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "guest proc");
    check(mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "guest sysfs");
    check(mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID, "mode=0755") == 0, "guest devtmpfs");
    check(modules(), "guest disk modules");
    loopback();
    wait_device("/dev/vda"); wait_device("/dev/vdb");
    check(mount("/dev/vda", "/newroot", "ext4", MS_RDONLY | MS_NOSUID | MS_NODEV, "noload") == 0,
          "guest immutable rootfs");
    puts("HF_IMAGE_ROOT readonly=1 filesystem=ext4 device=vda");
    check(mount("/dev/vdb", "/newroot/data", "ext4", MS_NOSUID | MS_NODEV, "errors=remount-ro") == 0,
          "guest persistent data");
    data_mounted = 1;
    puts("HF_IMAGE_DATA readwrite=1 filesystem=ext4 device=vdb");
    check(mount("proc", "/newroot/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "guest runtime proc");
    check(mount("sysfs", "/newroot/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "guest runtime sys");
    check(mount("devtmpfs", "/newroot/dev", "devtmpfs", MS_NOSUID, "mode=0755") == 0, "guest runtime dev");
    check(mount("tmpfs", "/newroot/tmp", "tmpfs", MS_NOSUID | MS_NODEV, "mode=1777,size=256m") == 0, "guest tmp");
    check(mount("tmpfs", "/newroot/run", "tmpfs", MS_NOSUID | MS_NODEV, "mode=0755,size=64m") == 0, "guest run");
    make_directory("/newroot/dev/shm", 01777, 0);
    check(mount("tmpfs", "/newroot/dev/shm", "tmpfs", MS_NOSUID | MS_NODEV,
                "mode=1777,size=128m") == 0, "guest shared memory");
    make_directory("/newroot/run/user", 0755, 0);
    make_directory("/newroot/run/user/1000", 0700, 1000);
    check(chown("/newroot/data", 1000, 1000) == 0, "guest persistent owner");
    for (const char **name = (const char *[]) {"home", "cache", "config", "userdata", NULL}; *name; ++name) {
        char path[64]; snprintf(path, sizeof path, "/newroot/data/%s", *name);
        make_directory(path, 0700, 1000);
    }
    int result = run_ui();
    printf("HF_IMAGE_CHILD phase=%s status=%d\n", phase, result);
    finish(result == 0);
    return 1;
}
