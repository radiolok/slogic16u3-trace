/* Trace source for BE-U1000: a known sequence of branches.
 *
 * Each loop pass: a conditional branch on the parity of n (inc_even / inc_odd), a call through a pointer from
 * ops[n % 3] (an indirect jump, so the message carries an address) and the return (indirect too). The trace must
 * show op_a, op_b, op_c in order with no gaps: ntrace_decode.py checks that.
 *
 * bits(i) is an inner loop of nbits steps (default 8) branching on the bits of i: 2 * nbits conditional branches
 * per pass. In BTM every taken one is a DirectBranch message, in HTM a HIST bit in the next IndirectBranchHist. */
#include <stdint.h>

volatile uint32_t n;        /* pass number, readable over SBA */
volatile uint32_t acc;
volatile uint32_t nbits = 8;  /* bits() loop length, can be changed over SBA on the fly */
volatile uint32_t pat = 0xFFFFFFFF;  /* if not 0xFFFFFFFF, bits() gets pat instead of i (known outcomes) */

__attribute__((noinline)) static void inc_even(void) { acc += 2; }
__attribute__((noinline)) static void inc_odd(void) { acc += 1; }
__attribute__((noinline)) static void op_a(void) { acc ^= 0x5A; }
__attribute__((noinline)) static void op_b(void) { acc = acc << 1 | acc >> 31; }
__attribute__((noinline)) static void op_c(void) { acc -= 3; }

static void (*const ops[3])(void) = { op_a, op_b, op_c };

__attribute__((noinline)) static uint32_t bits(uint32_t x)
{
    uint32_t r = 0;
    uint32_t len = nbits;
    for (uint32_t b = 0; b < len; b++) {
        if (x & (1u << (b & 31))) {
            r += b;
            __asm__ volatile("" ::: "memory");  /* keep the branch */
        } else {
            r ^= b;
            __asm__ volatile("" ::: "memory");
        }
    }
    return r;
}

int main(void)
{
    for (uint32_t i = 0;; i++) {
        n = i;
        if (i & 1)
            inc_odd();
        else
            inc_even();
        acc += bits(pat == 0xFFFFFFFF ? i : pat);
        ops[i % 3]();
    }
}
