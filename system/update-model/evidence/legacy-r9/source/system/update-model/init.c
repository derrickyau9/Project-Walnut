#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/reboot.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

static const char *attempt, *slot, *version, *health_mode, *capture, *dng_sha, *native_sha;
static int initialize_fixtures;
static int data_mounted;

static void finish(int passed) {
    sync();
    if (data_mounted) {
        if (umount("/newroot/data") != 0) { perror("update data unmount"); passed = 0; }
        else { data_mounted = 0; puts("HF_UPDATE_DATA_UNMOUNTED clean=1"); }
    }
    sync();
    printf("HF_UPDATE_RESULT %s attempt_id=%s slot=%s version=%s\n", passed ? "PASS" : "FAIL", attempt, slot, version);
    reboot(LINUX_REBOOT_CMD_POWER_OFF);
    for (;;) pause();
}

static void check(int okay, const char *operation) {
    if (!okay) { perror(operation); finish(0); }
}

static int token(const char *value, size_t limit) {
    if (!value || !*value || strlen(value) > limit) return 0;
    for (; *value; ++value)
        if (!((*value >= 'a' && *value <= 'z') || (*value >= 'A' && *value <= 'Z') ||
              (*value >= '0' && *value <= '9') || *value == '-' || *value == '_' || *value == '.')) return 0;
    return 1;
}

static int hex_string(const char *value, size_t length, int uuid) {
    if (!value || strlen(value) != length) return 0;
    for (size_t i = 0; i < length; ++i) {
        if (uuid && (i == 8 || i == 13 || i == 18 || i == 23)) { if (value[i] != '-') return 0; }
        else if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) return 0;
    }
    return 1;
}

static int load_modules(void) {
    FILE *list = fopen("/modules/load-order", "r");
    if (!list) return 0;
    char name[128], path[160];
    unsigned count = 0;
    while (fgets(name, sizeof name, list)) {
        size_t length = strlen(name);
        if (!length || name[length - 1] != '\n' || ++count > 32) { fclose(list); return 0; }
        name[--length] = 0;
        if (!token(name, 120) || strchr(name, '.')) { fclose(list); return 0; }
        if (snprintf(path, sizeof path, "/modules/%s.ko", name) >= (int)sizeof path) { fclose(list); return 0; }
        int fd = open(path, O_RDONLY | O_NOFOLLOW);
        if (fd < 0) { fclose(list); return 0; }
        int result = (int)syscall(SYS_finit_module, fd, "", 0), saved = errno;
        close(fd);
        if (result && saved != EEXIST) { errno = saved; perror(path); fclose(list); return 0; }
    }
    int okay = !ferror(list) && count > 0;
    fclose(list);
    return okay;
}

static void wait_device(const char *path) {
    struct stat info;
    for (int i = 0; i < 100; ++i) {
        if (stat(path, &info) == 0 && S_ISBLK(info.st_mode)) return;
        usleep(100000);
    }
    errno = ENODEV; check(0, path);
}

static void copy_health(void) {
    int source = open("/health.py", O_RDONLY | O_NOFOLLOW);
    struct stat info;
    check(source >= 0 && fstat(source, &info) == 0 && S_ISREG(info.st_mode) && info.st_size > 0 && info.st_size <= 65536,
          "update health source");
    int output = open("/newroot/run/update-health.py", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0444);
    check(output >= 0, "update health temporary runtime");
    char bytes[4096];
    ssize_t count;
    while ((count = read(source, bytes, sizeof bytes)) > 0) {
        ssize_t offset = 0;
        while (offset < count) { ssize_t wrote = write(output, bytes + offset, (size_t)(count - offset)); check(wrote > 0, "update health copy"); offset += wrote; }
    }
    check(count == 0 && fsync(output) == 0, "update health copy sync");
    check(close(source) == 0 && close(output) == 0, "update health copy close");
}

static int run_health(void) {
    pid_t child = fork();
    if (child < 0) return -1;
    if (!child) {
        if (chroot("/newroot") || chdir("/opt/hasslefree") || setgroups(0, NULL) || setgid(1000) || setuid(1000)) {
            perror("update normal user runtime"); _exit(126);
        }
        clearenv();
        setenv("PATH", "/usr/bin:/bin", 1); setenv("HOME", "/data/home", 1);
        setenv("LANG", "C.UTF-8", 1); setenv("LC_ALL", "C.UTF-8", 1);
        setenv("PYTHONPATH", "/opt/hasslefree", 1); setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("PYTHONNOUSERSITE", "1", 1);
        setenv("HF_UPDATE_GUEST", "1", 1);
        char *arguments[] = {"/usr/bin/python3", "-B", "/run/update-health.py",
            "--attempt-id", (char *)attempt, "--slot", (char *)slot, "--version", (char *)version,
            "--health-mode", (char *)health_mode, "--expected-capture-id", (char *)capture,
            "--expected-dng-sha", (char *)dng_sha, "--expected-native-sha", (char *)native_sha,
            initialize_fixtures ? "--initialize-fixtures" : NULL, NULL};
        printf("HF_UPDATE_RUNTIME uid=%ld attempt_id=%s\n", (long)getuid(), attempt);
        execv(arguments[0], arguments);
        perror("update ARM Python exec"); _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0) if (errno != EINTR) return -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0); setvbuf(stderr, NULL, _IONBF, 0);
    const char *marker = getenv("HASSLEFREE_ARM64_UPDATE");
    attempt = getenv("HF_UPDATE_ATTEMPT_ID"); slot = getenv("HF_UPDATE_SLOT");
    version = getenv("HF_UPDATE_VERSION"); health_mode = getenv("HF_UPDATE_HEALTH_MODE");
    capture = getenv("HF_UPDATE_CAPTURE_ID"); dng_sha = getenv("HF_UPDATE_DNG_SHA"); native_sha = getenv("HF_UPDATE_NATIVE_SHA");
    const char *initialize = getenv("HF_UPDATE_INITIALIZE_FIXTURES");
    initialize_fixtures = initialize && strcmp(initialize, "1") == 0;
    struct utsname machine;
    if (getpid() != 1 || !marker || strcmp(marker, "1") || uname(&machine) || strcmp(machine.machine, "aarch64") ||
        !hex_string(attempt, 36, 1) || !slot || (strcmp(slot, "A") && strcmp(slot, "B")) || !token(version, 64) ||
        !health_mode || (strcmp(health_mode, "healthy") && strcmp(health_mode, "fail")) ||
        !initialize || (strcmp(initialize, "0") && strcmp(initialize, "1")) ||
        !hex_string(capture, 36, 1) || !hex_string(dng_sha, 64, 0) || !hex_string(native_sha, 64, 0)) {
        fputs("HF_UPDATE_INIT guest-only: ARM64 PID 1 and explicit valid update identity required\n", stderr);
        return 2;
    }
    printf("HF_UPDATE_INIT pid=1 machine=aarch64 attempt_id=%s slot=%s version=%s\n", attempt, slot, version);
    check(mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "update proc");
    check(mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "update sys");
    check(mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID, "mode=0755") == 0, "update dev");
    check(load_modules(), "update disk modules");
    wait_device("/dev/vda"); wait_device("/dev/vdb");
    check(mount("/dev/vda", "/newroot", "ext4", MS_RDONLY | MS_NOSUID | MS_NODEV, "noload") == 0, "update root");
    puts("HF_UPDATE_ROOT readonly=1 filesystem=ext4 device=vda");
    check(mount("/dev/vdb", "/newroot/data", "ext4", MS_NOSUID | MS_NODEV, "errors=remount-ro") == 0, "update data");
    data_mounted = 1; puts("HF_UPDATE_DATA readwrite=1 filesystem=ext4 device=vdb");
    check(mount("proc", "/newroot/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "update runtime proc");
    check(mount("sysfs", "/newroot/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0, "update runtime sys");
    check(mount("devtmpfs", "/newroot/dev", "devtmpfs", MS_NOSUID, "mode=0755") == 0, "update runtime dev");
    check(mount("tmpfs", "/newroot/tmp", "tmpfs", MS_NOSUID | MS_NODEV, "mode=1777,size=128m") == 0, "update tmp");
    check(mount("tmpfs", "/newroot/run", "tmpfs", MS_NOSUID | MS_NODEV, "mode=0755,size=32m") == 0, "update run");
    check(chown("/newroot/data", 1000, 1000) == 0, "update data owner");
    copy_health();
    int result = run_health();
    printf("HF_UPDATE_CHILD status=%d attempt_id=%s\n", result, attempt);
    finish(result == 0);
    return 1;
}
