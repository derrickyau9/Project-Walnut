/* Host smoke test of NetBSD's unmodified STBI decoder; not a kernel test. */
#include <limits.h>
#define STBI_NO_HDR
#include "netbsd-11/sys/dev/stbi/stb_image.c"

int main(int argc, char **argv)
{
    int width, height, components, w4, h4, c4;
    unsigned char *rgb, *rgba;
    size_t pixels, i;
    FILE *out;
    if (argc != 3) return 2;
    rgb = stbi_load(argv[1], &width, &height, &components, STBI_rgb);
    if (!rgb) {
        fprintf(stderr, "%s\n", stbi_failure_reason());
        return 3;
    }
    rgba = stbi_load(argv[1], &w4, &h4, &c4, STBI_rgb_alpha);
    if (!rgba || w4 != width || h4 != height || c4 != components) return 4;
    pixels = (size_t)width * height;
    for (i = 0; i < pixels; i++) {
        if (memcmp(rgb + i * 3, rgba + i * 4, 3) || rgba[i * 4 + 3] != 255)
            return 5;
    }
    out = fopen(argv[2], "wb");
    if (!out || fwrite(rgba, 4, pixels, out) != pixels || fclose(out)) return 6;
    printf("{\"width\":%d,\"height\":%d,\"source_components\":%d,"
           "\"rgb_and_rgba_colors_equal\":true,\"alpha_all_255\":true}\n",
           width, height, components);
    stbi_image_free(rgb);
    stbi_image_free(rgba);
    return 0;
}
