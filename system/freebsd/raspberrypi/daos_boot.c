/* SPDX-License-Identifier: BSD-2-Clause
 * Copyright (c) 2026 DaOS contributors.
 * Early 800x480 framebuffer splash, until Qt reports its first splash frame.
 */
#include <sys/types.h>
#include <sys/fbio.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stop;
static void terminate(int n) { (void)n;stop=1; }
int main(int argc,char **argv) {
    struct fbtype fb;
    struct fb_rgboffs rgb;
    unsigned stride=0;
    int fd=-1;
    for(int i=0;i<100 && fd<0;i++){fd=open("/dev/fb0",O_RDWR);if(fd<0)usleep(100000);}
    if(fd<0 || ioctl(fd,FBIOGTYPE,&fb)<0 || ioctl(fd,FBIO_GETLINEWIDTH,&stride)<0){perror("DaOS framebuffer");return 1;}
    if(ioctl(fd,FBIO_GETRGBOFFS,&rgb)<0) {
        int vt=open("/dev/ttyv0",O_RDONLY);
        if(vt<0 || ioctl(vt,FBIO_GETRGBOFFS,&rgb)<0) {
            /* Pi4 EFI BGRX fallback; fbd(4) does not implement GETRGBOFFS. */
            rgb.red=16;rgb.green=8;rgb.blue=0;
        }
        if(vt>=0)close(vt);
    }
    if(argc==2 && !strcmp(argv[1],"--info")) {
        printf("{\"width\":%d,\"height\":%d,\"stride\":%u,\"depth\":%d,\"rgb\":[%d,%d,%d]}\n",fb.fb_width,fb.fb_height,stride,fb.fb_depth,rgb.red,rgb.green,rgb.blue);close(fd);return 0;
    }
    if(argc!=2 || fb.fb_width!=800 || fb.fb_height!=480 || stride<3200 || stride>16384 || fb.fb_size<480*(int)stride || (fb.fb_depth!=24 && fb.fb_depth!=32))return 2;
    if(rgb.red<0 || rgb.red>24 || rgb.green<0 || rgb.green>24 || rgb.blue<0 || rgb.blue>24)return 2;
    FILE *asset=fopen(argv[1],"rb");if(!asset)return 3;
    unsigned char *input=malloc(800*480*3);
    uint32_t *frame=malloc(800*480*4);
    if(!input || !frame || fread(input,1,800*480*3,asset)!=800*480*3){fclose(asset);return 3;}
    fclose(asset);
    for(unsigned i=0;i<800*480;i++)frame[i]=((uint32_t)input[i*3]<<rgb.red)|((uint32_t)input[i*3+1]<<rgb.green)|((uint32_t)input[i*3+2]<<rgb.blue);
    free(input);
    unsigned char *map=mmap(NULL,fb.fb_size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(map==MAP_FAILED){perror("DaOS framebuffer mmap");return 4;}
    signal(SIGTERM,terminate);signal(SIGINT,terminate);
    unsigned tick=0,progress=20;
    time_t start=time(NULL);
    while(!stop && time(NULL)-start<240) {
        struct stat st;
        if(stat("/var/run/DaOS-boot-native.stop",&st)==0)break;
        FILE *phase=fopen("/var/run/DaOS-boot-stage","r");unsigned value;
        if(phase){if(fscanf(phase,"%u",&value)==1 && value>progress && value<=95)progress=value;fclose(phase);}
        for(unsigned y=0;y<480;y++)memcpy(map+y*stride,frame+y*800,3200);
        unsigned pulse=(tick++*30)%700;
        for(unsigned y=468;y<472;y++)for(unsigned x=100;x<700;x++) {
            unsigned shade=(x-100)<6*progress?170:51;
            if(x>=pulse && x<pulse+100)shade=255;
            uint32_t color=(shade<<rgb.red)|(shade<<rgb.green)|(shade<<rgb.blue);
            memcpy(map+y*stride+x*4,&color,4);
        }
        usleep(100000);
    }
    munmap(map,fb.fb_size);close(fd);free(frame);return 0;
}
