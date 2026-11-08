/* Minimal ARM64 virtual-target init. No host mounts, network or camera hardware. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_DNG 65536
static unsigned char original[MAX_DNG];
int hf_rpc_capture(void);

static void finish(int ok) {
    printf("HF_QEMU_RESULT %s synthetic-only\n", ok ? "PASS" : "FAIL");
    fflush(stdout);
    sync();
    reboot(RB_POWER_OFF);
    for (;;) pause();
}

static int execute(char *const argv[]) {
    pid_t child = fork();
    if (child == -1) return -1;
    if (child == 0) {
        execv(argv[0], argv);
        perror("guest exec");
        _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) == -1) {
        if (errno != EINTR) return -1;
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    const char *guest=getenv("HASSLEFREE_QEMU_VIRT");
    if (getpid()!=1 || !guest || strcmp(guest,"1")!=0) {
        fputs("HF_QEMU_INIT guest-only: PID 1 and explicit virtual-kernel marker required\n",stderr);
        return 2; /* Never mount or attempt reboot in a host/user-mode invocation. */
    }
    struct utsname version;
    if (uname(&version) != 0 || strcmp(version.machine, "aarch64") != 0) finish(0);
    printf("HF_QEMU_INIT pid=1 machine=%s release=%s synthetic-only\n", version.machine, version.release);
    if (mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) != 0) finish(0);
    char *core[] = {"/bin/hf-core-tests", NULL};
    char *authority[] = {"/bin/hf-daemon-tests", NULL};
    if (execute(core) != 0 || execute(authority) != 0) finish(0);
    printf("HF_QEMU_NATIVE_TESTS_OK\n");
    char *worker[] = {"/bin/hf-native-worker", "--output", "/photos/first.dng",
        "--width", "128", "--height", "96", "--exposure-us", "12500", "--gain", "1.5",
        "--scene", "scene", "--request-id", "26110500-0000-4000-8000-000000000001",
        "--sensor-id", "HF-Sim-QEMU-virt", "--calibration-id", "synthetic-uncalibrated", NULL};
    if (execute(worker) != 0) finish(0);
    int file = open("/photos/first.dng", O_RDONLY | O_NOFOLLOW);
    struct stat info;
    if (file < 0 || fstat(file, &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 8 || info.st_size > MAX_DNG) finish(0);
    size_t size = (size_t)info.st_size, offset = 0;
    while (offset < size) {
        ssize_t count = read(file, original + offset, size - offset);
        if (count <= 0) finish(0);
        offset += (size_t)count;
    }
    close(file);
    if (execute(worker) == 0) finish(0); /* Existing output must never be overwritten. */
    file = open("/photos/first.dng", O_RDONLY | O_NOFOLLOW);
    if (file < 0 || fstat(file, &info) != 0 || (size_t)info.st_size != size) finish(0);
    unsigned char comparison[512];
    offset = 0;
    while (offset < size) {
        size_t wanted = size - offset < sizeof comparison ? size - offset : sizeof comparison;
        ssize_t count = read(file, comparison, wanted);
        if (count <= 0 || memcmp(comparison, original + offset, (size_t)count) != 0) finish(0);
        offset += (size_t)count;
    }
    close(file);
    printf("HF_QEMU_NOREPLACE_OK\n");
    if (!hf_rpc_capture()) finish(0);
    file=open("/camera/photos/26110500-0000-4000-8000-000000000002.dng",O_RDONLY|O_NOFOLLOW);
    if (file<0 || fstat(file,&info)!=0 || !S_ISREG(info.st_mode) || info.st_size<8 || info.st_size>MAX_DNG) finish(0);
    size=(size_t)info.st_size; offset=0;
    while (offset<size) {
        ssize_t count=read(file,original+offset,size-offset);
        if (count<=0) finish(0);
        offset+=(size_t)count;
    }
    close(file);
    printf("HF_QEMU_DNG_BEGIN %zu\n", size);
    for (size_t i = 0; i < size; i += 48) {
        printf("HF_QEMU_DNG ");
        size_t end = i + 48 < size ? i + 48 : size;
        for (size_t j = i; j < end; ++j) printf("%02x", original[j]);
        printf("\n");
    }
    printf("HF_QEMU_DNG_END\n");
    finish(1);
}
