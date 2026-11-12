"""Explicit candidate settings for the normal Pi4 loader/session path only."""
import re

LOADER={'autoboot_delay':'0'}
RC={'DaOS_camera_fastboot':'YES'}


def edit(text, values):
    lines=[line for line in text.splitlines()
           if line!='# Project Walnut power-on candidate'
           and not any(re.match(r'\s*'+re.escape(key)+r'\s*=',line) for key in values)]
    return '\n'.join(lines)+'\n# Project Walnut power-on candidate\n'+''.join(key+'="'+value+'"\n' for key,value in values.items())


def normal_boot_configuration(loader, rc):
    return edit(loader,LOADER),edit(rc,RC)
