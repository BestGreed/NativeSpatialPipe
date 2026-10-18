/* Runs only in the separate telemetry process. One pass over strided PCM rows;
   no allocations, threads, I/O, or changes to capture/render scheduling. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <math.h>

__declspec(dllexport) int nsp_meter_f32(const unsigned char *data, size_t bytes,
        size_t first, size_t stride, uint32_t rows, double *result) {
    if (!data || !result || !rows || rows > 160 || stride < 480 * sizeof(float)
            || first > bytes || bytes-first < 480*sizeof(float)
            || (rows-1) > (bytes-first-480*sizeof(float))/stride) return -1;
    for (uint32_t row=0; row<rows; row++) {
        double sum=0.0, correction=0.0, peak=0.0;
        const unsigned char *samples=data+first+row*stride;
        for (uint32_t i=0; i<480; i++) {
            float input;
            memcpy(&input, samples+i*sizeof(input), sizeof(input));
            if (!isfinite(input)) return 0;
            double value=(double)input, square=value*value;
            double next=sum+square;
            correction += fabs(sum)>=fabs(square) ? (sum-next)+square : (square-next)+sum;
            sum=next;
            double magnitude=fabs(value);
            if (magnitude>peak) peak=magnitude;
        }
        result[row*2]=sum+correction;
        result[row*2+1]=peak;
    }
    return 1;
}
