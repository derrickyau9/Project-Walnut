/* DaOS kernel-hang watchdog. Build static against the pinned FreeBSD 15 SDK.
 * The BCM2835/2711 backend accepts at most 15 seconds; 8 seconds is the largest
 * supported power-of-two timeout (a 32-second request cannot arm that driver).
 * This program is optional, not a substitute for the armed FAT boot selector.
 */
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/mount.h>
#include <sys/ioctl.h>
#include <sys/time.h>
#include <sys/watchdog.h>
#include <errno.h>
#include <fcntl.h>
#include <kenv.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const char identity[] = "DaOS-kernel-hang-watchdog-v1";
static const char ram[] = "/DaOS-recovery-ram";
static const char marker[] = "/etc/daos/recovery-watchdog.enabled";
static const char policy[] = "DaOS-recovery-watchdog-v1\n";
static volatile sig_atomic_t stop_requested;

static void
stop_cleanly(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

static int
enabled(void)
{
    char mode[32], data[sizeof(policy)];
    struct stat info;
    int fd;
    ssize_t count;

    if (kenv(KENV_GET, "daos_recovery", mode, sizeof(mode)) > 0 &&
        strcmp(mode, "YES") == 0)
        return (0);
    fd = open(marker, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0)
        return (0);
    count = read(fd, data, sizeof(data));
    if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_uid != 0 || info.st_nlink != 1 || (info.st_mode & 0222) != 0 ||
        info.st_size != (off_t)(sizeof(policy) - 1) ||
        count != (ssize_t)(sizeof(policy) - 1) ||
        memcmp(data, policy, sizeof(policy) - 1) != 0) {
        close(fd);
        return (0);
    }
    close(fd);
    return (1);
}

int
main(int argc, char **argv)
{
    struct stat info;
    struct statfs filesystem;
    struct sigaction action;
    sbintime_t current = 0, timeout = 8 * SBT_1S;
    int fd, lock, soft = 0, control = WD_CTRL_ENABLE;

    (void)argv;
    fprintf(stderr, "%s\n", identity);
    if (argc != 1 || geteuid() != 0 || !enabled()) {
        fprintf(stderr, "REFUSED: requires root, immutable enabled policy and normal boot\n");
        return (64);
    }
    if (lstat(ram, &info) != 0 || !S_ISDIR(info.st_mode) ||
        info.st_uid != 0 || (info.st_mode & 0777) != 0700 ||
        statfs(ram, &filesystem) != 0 ||
        strcmp(filesystem.f_fstypename, "tmpfs") != 0) {
        fprintf(stderr, "REFUSED: dedicated recovery tmpfs unavailable\n");
        return (65);
    }
    lock = open("/DaOS-recovery-ram/kernel-watchdog.lock",
        O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (lock < 0 || fstat(lock, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_uid != 0 || info.st_nlink != 1 || (info.st_mode & 0777) != 0600)
        return (66);
    if (flock(lock, LOCK_EX | LOCK_NB) != 0) {
        int lock_error = errno;
        close(lock);
        return (lock_error == EWOULDBLOCK ? 0 : 67);
    }
    memset(&action, 0, sizeof(action));
    action.sa_handler = stop_cleanly;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) != 0 ||
        sigaction(SIGINT, &action, NULL) != 0)
        return (68);
    signal(SIGHUP, SIG_IGN);
    fd = open("/dev/fido", O_RDWR | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, WDIOC_GETTIMEOUT, &current) != 0 || current != 0) {
        fprintf(stderr, "REFUSED: watchdog missing, busy or already armed\n");
        return (69);
    }
    if (ioctl(fd, WDIOC_SETSOFT, &soft) != 0 ||
        ioctl(fd, WDIOC_SETTIMEOUT, &timeout) != 0 ||
        ioctl(fd, WDIOC_CONTROL, &control) != 0 ||
        ioctl(fd, WDIOC_SETTIMEOUT, &timeout) != 0) {
        /* A partial arm must not be silently converted to a successful start. */
        fprintf(stderr, "REFUSED: hardware watchdog arm failed; inspect/reset before retry\n");
        return (70);
    }
    fprintf(stderr, "ARMED pid=%ld hardware_timeout_seconds=8 feed_seconds=1\n",
        (long)getpid());
    fflush(stderr);
    while (!stop_requested) {
        if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) != 0) {
            fprintf(stderr, "FEED_FAILED: leaving watchdog armed\n");
            return (71);
        }
        sleep(1);
    }
    control = WD_CTRL_DISABLE;
    if (ioctl(fd, WDIOC_CONTROL, &control) != 0) {
        fprintf(stderr, "DISARM_FAILED: watchdog remains armed\n");
        return (72);
    }
    fprintf(stderr, "DISARMED: clean SIGTERM/SIGINT shutdown\n");
    close(fd);
    close(lock);
    return (0);
}
