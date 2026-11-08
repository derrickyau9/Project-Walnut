"""Compile and execute native watchdog control logic with every hardware call mocked."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
WATCHDOG_HEADER='''#include <stdint.h>
typedef int64_t sbintime_t;
#define SBT_1S ((sbintime_t)1 << 32)
#define WDIOC_GETTIMEOUT 101
#define WDIOC_SETSOFT 102
#define WDIOC_SETTIMEOUT 103
#define WDIOC_CONTROL 104
#define WD_CTRL_ENABLE 1
#define WD_CTRL_DISABLE 2
'''
MOUNT_HEADER='''struct statfs { char f_fstypename[16]; };
int statfs(const char *, struct statfs *);
'''
KENV_HEADER='''#define KENV_GET 0
int kenv(int, const char *, char *, int);
'''
HARNESS=r'''
#define open mock_open
#define close mock_close
#define read mock_read
#define fstat mock_fstat
#define lstat mock_lstat
#define statfs mock_statfs
#define geteuid mock_geteuid
#define ioctl mock_ioctl
#define kenv mock_kenv
#define flock mock_flock
#define sleep mock_sleep
#define main watchdog_main
#include "__SOURCE__"
#undef main
static const char *scenario;
static int timeouts;
uid_t mock_geteuid(void) { return 0; }
int mock_open(const char *path, int flags, ...) {
    (void)flags;
    if (strcmp(path, "/etc/daos/recovery-watchdog.enabled") == 0) {
        if (strcmp(scenario,"disabled")==0) { errno=ENOENT; return -1; }
        return 3;
    }
    if (strcmp(path,"/DaOS-recovery-ram/kernel-watchdog.lock")==0) return 4;
    if (strcmp(path,"/dev/fido")==0) { puts("OPEN_FIDO"); return 5; }
    abort();
}
int mock_close(int fd) { (void)fd;return 0; }
ssize_t mock_read(int fd, void *out, size_t count) {
    (void)fd;(void)count;memcpy(out,policy,sizeof(policy)-1);return sizeof(policy)-1;
}
int mock_fstat(int fd, struct stat *info) {
    memset(info,0,sizeof(*info));info->st_uid=0;info->st_nlink=1;
    info->st_mode=S_IFREG|(fd==3?0444:0600);info->st_size=sizeof(policy)-1;
    if (strcmp(scenario,"writable-policy")==0 && fd==3)info->st_mode|=0200;
    return 0;
}
int mock_lstat(const char *name,struct stat *info) {
    (void)name;memset(info,0,sizeof(*info));info->st_uid=0;info->st_mode=S_IFDIR|0700;return 0;
}
int mock_statfs(const char *path,struct statfs *info) {
    (void)path;strcpy(info->f_fstypename,"tmpfs");return 0;
}
int mock_kenv(int action,const char *name,char *out,int len) {
    (void)action;(void)name;(void)len;
    if (strcmp(scenario,"rescue")==0) { strcpy(out,"YES");return 4; }
    errno=ENOENT;return -1;
}
int mock_flock(int fd,int flags) { (void)fd;(void)flags;return 0; }
int mock_ioctl(int fd,unsigned long request,...) {
    va_list args;void *value;(void)fd;
    va_start(args,request);value=va_arg(args,void *);va_end(args);
    if (request==WDIOC_GETTIMEOUT) {
        puts("GET_TIMEOUT");
        *(sbintime_t *)value=strcmp(scenario,"already-armed")==0?SBT_1S:0;
    } else if (request==WDIOC_SETTIMEOUT) {
        if (*(sbintime_t *)value!=8*SBT_1S)abort();
        puts("SET_TIMEOUT_8");timeouts++;
        if (strcmp(scenario,"feed-error")==0 && timeouts>=3)return -1;
    } else if (request==WDIOC_SETSOFT) {
        if (*(int *)value!=0)abort();
        puts("SET_HARDWARE");
    } else if (request==WDIOC_CONTROL) {
        if (*(int *)value==WD_CTRL_ENABLE) {
            puts("ENABLE");if (strcmp(scenario,"arm-error")==0)return -1;
        } else if (*(int *)value==WD_CTRL_DISABLE)puts("DISABLE");else abort();
    } else abort();
    return 0;
}
unsigned mock_sleep(unsigned seconds) {
    if (seconds!=1)abort();
    stop_cleanly(SIGTERM);return 0;
}
int main(int argc,char **argv) {
    if (argc!=2)return 99;
    scenario=argv[1];return watchdog_main(1,argv);
}
'''


@unittest.skipIf(os.name=='nt' or not shutil.which('cc'),'Requires POSIX host C compiler')
class WatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='daos-watchdog-logic-')
        cls.root=Path(cls.temp.name);(cls.root/'sys').mkdir()
        (cls.root/'sys/watchdog.h').write_text(WATCHDOG_HEADER)
        (cls.root/'sys/mount.h').write_text(MOUNT_HEADER)
        (cls.root/'kenv.h').write_text(KENV_HEADER)
        harness='#include <stdarg.h>\n#include <stdlib.h>\n'+HARNESS.replace('__SOURCE__',str(HERE/'kernel_watchdog.c'))
        (cls.root/'harness.c').write_text(harness)
        compile=subprocess.run(['cc','-Wall','-Wextra','-Werror','-I',str(cls.root),str(cls.root/'harness.c'),'-o',str(cls.root/'watchdog-test')],capture_output=True,text=True)
        if compile.returncode:raise RuntimeError(compile.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_mode(self,mode):
        return subprocess.run([str(self.root/'watchdog-test'),mode],capture_output=True,text=True,timeout=3)

    def test_disabled_rescue_and_writable_policy_never_open_hardware(self):
        for mode in ('disabled','rescue','writable-policy'):
            with self.subTest(mode=mode):
                p=self.run_mode(mode);self.assertEqual(p.returncode,64)
                self.assertNotIn('OPEN_FIDO',p.stdout);self.assertNotIn('ARMED pid=',p.stderr)

    def test_existing_watchdog_is_not_reconfigured(self):
        p=self.run_mode('already-armed');self.assertEqual(p.returncode,69)
        self.assertNotIn('ENABLE',p.stdout);self.assertNotIn('DISABLE',p.stdout)

    def test_partial_arm_failure_is_not_reported_as_armed(self):
        p=self.run_mode('arm-error');self.assertEqual(p.returncode,70)
        self.assertNotIn('ARMED pid=',p.stderr);self.assertNotIn('DISABLE',p.stdout)

    def test_clean_term_disarms_after_successful_hardware_arm(self):
        p=self.run_mode('clean');self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('ARMED pid=',p.stderr);self.assertIn('DISARMED:',p.stderr)
        self.assertEqual(p.stdout.splitlines(),['OPEN_FIDO','GET_TIMEOUT','SET_HARDWARE','SET_TIMEOUT_8','ENABLE','SET_TIMEOUT_8','SET_TIMEOUT_8','DISABLE'])

    def test_feed_failure_leaves_hardware_armed(self):
        p=self.run_mode('feed-error');self.assertEqual(p.returncode,71)
        self.assertIn('FEED_FAILED',p.stderr);self.assertNotIn('DISABLE',p.stdout)


if __name__=='__main__':unittest.main()
