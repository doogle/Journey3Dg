/*
 * Brute force search of optimal DIV and TOP register values for RP2040 PWM
 *
 * Build and run with:
 *   gcc -fopenmp -O3 -o genfreqs genfreqs.c -lm && time ./genfreqs

 * Copyright 2022 David Steinberg <david@sonabuzz.com>
 */

/*
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */


#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <stdint.h>

#define SYS_FREQ_HZ     280000000
#define BASE_NOTE       (69-24)
#define OCTAVES         5

#define NOTE_CNT        (OCTAVES * 12)

typedef struct _note {
    double freq;
    uint32_t top;
    uint32_t div;
    double divf;
    double out_freq;
    double err;
} note;

note notes[NOTE_CNT];

int main(void)
{
    note *n = notes;
    for (int i=0; i<NOTE_CNT; i++, n++) {
        n->freq = 440. * pow(2, (i + BASE_NOTE - 69)/12.);
        n->err = 9999999999;
        n->out_freq = -1;
    }

    #pragma omp parallel for
    for (uint32_t div=1<<4; div<(1<<12); div++) {
        double divf = (double)div * 0.0625;
        printf("\r%6.2lf    ", divf);
        double freq_div = (double)SYS_FREQ_HZ / divf;
        for (uint32_t top=0; top<65536; top++) {
            double out_freq = freq_div / (double)(top + 1);
            n = notes;
            for (int i=0; i<NOTE_CNT; i++, n++) {
                double err = fabs(n->freq - out_freq);
                if (err < n->err) {
                    n->out_freq = out_freq;
                    n->err = err;
                    n->top = top;
                    n->div = div;
                    n->divf = divf;
                }
            }
        }
    }
    printf("\r             \n");

    n = notes;
    for (unsigned i=0; i<NOTE_CNT; i++, n++) {
        if (n->out_freq == -1) {
            printf("%u: no config\n", i);
            continue;
        }
        printf("%2u: %7.2lf -> %7.2lf (err=%4.2lf) : 0x%04x (%6.2lf) , 0x%04x\n",
            i, n->freq, n->out_freq, n->err, n->div, n->divf, n->top);
    }

    n = notes;
    printf("# This table is generated using genfreqs.c\n");
    printf("\npwm_cfgs = [");
    for (unsigned i=0; i<NOTE_CNT; i++, n++) {
        printf("(0x%x,0x%x)", n->div, n->top);
        if (i < (NOTE_CNT-1))
            printf(",");
        if ((i % 7 == 6) && (i < (NOTE_CNT-1)))
            printf("\n            ");
    }
    printf("]\n");

    return 0;
}
