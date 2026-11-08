/* Private FreeBSD-only read-only FIOBMAP2 probe, not a physical installer. */
#include <sys/types.h>
#include <sys/param.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/filio.h>
#include <sys/ioctl.h>
#include <err.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
int main(int argc,char **argv) {
    struct stat st;
    struct statfs fs;
    struct fiobmap2_arg map;
    if(argc!=2)errx(1,"one private fixture file required");
    int fd=open(argv[1],O_RDONLY|O_NOFOLLOW);
    if(fd<0||fstat(fd,&st)||fstatfs(fd,&fs))err(1,"read-only file stat");
    if(!S_ISREG(st.st_mode)||st.st_nlink!=1||st.st_uid!=0||strcmp(fs.f_fstypename,"ufs"))errx(1,"owned UFS ordinary file required");
    if(st.st_size!=512*1024||st.st_blksize<=0||(uint64_t)st.st_blksize!=fs.f_iosize||fs.f_iosize<4096||fs.f_iosize%512||st.st_size%fs.f_iosize)errx(1,"full fsblock geometry required");
    printf("{\"inode\":%ju,\"bytes\":%jd,\"st_blksize\":%jd,\"statfs_f_bsize\":%ju,\"statfs_f_iosize\":%ju,\"maps\":[",(uintmax_t)st.st_ino,(intmax_t)st.st_size,(intmax_t)st.st_blksize,(uintmax_t)fs.f_bsize,(uintmax_t)fs.f_iosize);
    for(int64_t n=0;n<st.st_size/(int64_t)fs.f_iosize;n++) {
        memset(&map,0,sizeof(map));map.bn=n;
        if(ioctl(fd,FIOBMAP2,&map))err(1,"FIOBMAP2");
        if(map.bn<=0)errx(1,"hole or invalid mapped block");
        printf("%s{\"logical\":%jd,\"sector512\":%jd,\"runp\":%d,\"runb\":%d}",n?",":"",(intmax_t)n,(intmax_t)map.bn,map.runp,map.runb);
    }
    puts("]}");close(fd);return 0;
}
