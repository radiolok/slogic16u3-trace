// Trace source for slogic16u3-trace on STM32F4-Discovery (STM32F407). ITM writes a 32-bit counter n to stimulus
// port 0 (packet 03 + 4 bytes); ETM (if cfg->etm) traces instructions; TPIU sends both (ATB IDs 1 and 2) out of
// the parallel trace port: PE2 TRACECLK -> CH12, PE3..PE6 TRACED0..3 -> CH8..CH11.
// Values go in bursts of cfg->burst with a pause in between, where TPIU sends syncs. n runs on across bursts,
// so the receiver can check continuity.
//
// TRACECLK = HCLK / 2, data on both edges. The mode is read from RAM (struct cfg at 0x20000000, written by
// stm32cfg.py through OpenOCD): SYSCLK source (HSI 16 MHz, HSE 8 MHz, PLL from HSE: n/2 MHz), AHB divider
// 1..512 (not 32), port width 1/2/4. After power-on: HSI / 512, HCLK 31.25 kHz, TRACECLK 15.6 kHz.
// The firmware re-applies the trace setup if a debugger resets it (OpenOCD touches DEMCR/DWT on connect).
// Green PD12 blinks while running; red PD14 = setup error (cfg->err).
#include <stdint.h>

#define REG(a) (*(volatile uint32_t *)(a))
#define RCC_CR REG(0x40023800)
#define RCC_PLLCFGR REG(0x40023804)
#define RCC_CFGR REG(0x40023808)
#define RCC_AHB1ENR REG(0x40023830)
#define FLASH_ACR REG(0x40023C00)
#define GPIOD 0x40020C00
#define GPIOE 0x40021000
#define MODER 0x00
#define OSPEEDR 0x08
#define ODR 0x14
#define AFRL 0x20
#define DBGMCU_CR REG(0xE0042004)
#define DEMCR REG(0xE000EDFC)
#define DWT_CTRL REG(0xE0001000)
#define ITM_STIM0 REG(0xE0000000)
#define ITM_TER REG(0xE0000E00)
#define ITM_TPR REG(0xE0000E40)
#define ITM_TCR REG(0xE0000E80)
#define ITM_LAR REG(0xE0000FB0)
#define TPI_CSPSR REG(0xE0040004)
#define TPI_SPPR REG(0xE00400F0)
#define TPI_FFCR REG(0xE0040304)
#define ETM_CR REG(0xE0041000)
#define ETM_TRIGGER REG(0xE0041008)
#define ETM_SR REG(0xE0041010)
#define ETM_TEEVR REG(0xE0041020)
#define ETM_TECR1 REG(0xE0041024)
#define ETM_FFLR REG(0xE004102C)
#define ETM_IDR REG(0xE00411E4)
#define ETM_TRACEIDR REG(0xE0041200)
#define ETM_LAR REG(0xE0041FB0)
#define ETM_CR_VAL (1u << 11 | 1u << 7)  // ETM port enabled, stall the core when the FIFO is full

#define MAGIC 0x544E4C52u  // "RLNT"
#define ITM_TCR_VAL (1u << 16 | 1u << 2 | 1u)  // ATB ID 1, SYNCENA, ITMENA

enum { SRC_HSI, SRC_HSE, SRC_PLL };

struct cfg {  // offsets are used by stm32cfg.py
    uint32_t magic;    // 0x00
    uint32_t seq;      // 0x04 changed by the host after writing the fields below
    uint32_t src;      // 0x08 SRC_*
    uint32_t pll_n;    // 0x0C PLL: VCO = n MHz (HSE / 8 * n), SYSCLK = n / 2 MHz, n 100..336
    uint32_t hdiv;     // 0x10 AHB divider: 1 2 4 8 16 64 128 256 512
    uint32_t width;    // 0x14 port width: 1 2 4
    uint32_t burst;    // 0x18 values per burst
    uint32_t idle;     // 0x1C pause between bursts, empty loop passes
    uint32_t applied;  // 0x20 seq applied
    uint32_t hclk;     // 0x24 HCLK, Hz (calculated)
    uint32_t err;      // 0x28 bit 0: HSE failed, 1: PLL failed, 2: bad hdiv
    uint32_t n;        // 0x2C next counter value
    uint32_t reinit;   // 0x30 how many times the trace setup was re-applied
    uint32_t ospeed;   // 0x34 PE2..PE6 slew rate (OSPEEDR): 0 low ... 3 very high
    uint32_t etm;      // 0x38 1: ETM instruction trace (ID 2)
    uint32_t etmidr;   // 0x3C ETMIDR, for the decoder (branch address encoding)
};
#define CFG ((volatile struct cfg *)0x20000000)

static int wait_bit(uint32_t mask, uint32_t want)
{
    for (uint32_t t = 0; t < 1000000; t++)
        if ((RCC_CR & mask) == want)
            return 1;
    return 0;
}

static uint32_t hpre(uint32_t div)
{
    static const uint16_t d[] = {2, 4, 8, 16, 64, 128, 256, 512};
    if (div == 1)
        return 0;
    for (uint32_t i = 0; i < 8; i++)
        if (d[i] == div)
            return 8 + i;
    return 0xFF;
}

static void set_sw(uint32_t sw, uint32_t hp)
{
    RCC_CFGR = (RCC_CFGR & ~0xF3u) | hp << 4 | sw;
    while ((RCC_CFGR >> 2 & 3u) != sw)
        ;
}

static void apply_clock(void)
{
    uint32_t src = CFG->src, err = 0, base = 16000000;
    uint32_t hp = hpre(CFG->hdiv);
    if (hp == 0xFF) {
        err |= 4;
        hp = 15;
    }
    // via HSI / 1, flash wait states for the worst case (5 for 168 MHz, fine for any)
    FLASH_ACR = 5u | 1u << 8 | 1u << 9 | 1u << 10;
    while ((FLASH_ACR & 7u) != 5u)
        ;
    set_sw(0, 0);
    RCC_CR &= ~(1u << 24);  // PLLOFF
    wait_bit(1u << 25, 0);
    RCC_CFGR = (RCC_CFGR & ~0xFC00u) | 5u << 10 | 4u << 13;  // APB1 / 4, APB2 / 2
    if (src == SRC_HSE || src == SRC_PLL) {
        RCC_CR |= 1u << 16;
        if (!wait_bit(1u << 17, 1u << 17)) {
            err |= 1;
            src = SRC_HSI;
        }
    }
    if (src == SRC_PLL) {
        uint32_t n = CFG->pll_n;
        n = n < 100 ? 100 : n > 336 ? 336 : n;
        RCC_PLLCFGR = 8u | n << 6 | 0u << 16 | 1u << 22 | 7u << 24;  // M 8, P 2, HSE, Q 7
        RCC_CR |= 1u << 24;
        if (wait_bit(1u << 25, 1u << 25))
            base = n * 500000u;
        else {
            err |= 2;
            src = SRC_HSI;
        }
    } else if (src == SRC_HSE)
        base = 8000000;
    set_sw(src == SRC_PLL ? 2 : src == SRC_HSE ? 1 : 0, hp);
    CFG->hclk = base >> (hp >= 12 ? hp - 6 : hp >= 8 ? hp - 7 : 0);
    CFG->err = err;
    REG(GPIOD + ODR) = (REG(GPIOD + ODR) & ~(1u << 14)) | (err ? 1u << 14 : 0);
}

static void apply_trace(void)
{
    uint32_t w = CFG->width;
    if (w != 1 && w != 2)
        w = 4;
    // PE2..PE6: AF0 (TRACECLK, TRACED0..3), slew rate from cfg
    uint32_t sp = CFG->ospeed > 3 ? 3 : CFG->ospeed;
    REG(GPIOE + AFRL) &= ~0x0FFFFF00u;
    REG(GPIOE + OSPEEDR) = (REG(GPIOE + OSPEEDR) & ~0x3FF0u) | sp * 0x1550u;
    REG(GPIOE + MODER) = (REG(GPIOE + MODER) & ~0x3FF0u) | 0x2AA0u;
    // synchronous port: TRACE_IOEN, TRACE_MODE 1/2/3 = 1/2/4 bits
    DBGMCU_CR = (DBGMCU_CR & ~0xE0u) | 1u << 5 | (w == 4 ? 3u : w) << 6;
    DEMCR |= 1u << 24;  // TRCENA
    TPI_CSPSR = 1u << (w - 1);
    TPI_SPPR = 0;          // parallel port
    TPI_FFCR = 0x102;      // formatter, continuous mode
    DWT_CTRL = (DWT_CTRL & ~0xC00u) | 1u << 10 | 1u;  // CYCCNT, SYNCTAP = CYCCNT[24]
    ITM_LAR = 0xC5ACCE55;
    ITM_TCR = ITM_TCR_VAL;
    ITM_TPR = 0;
    ITM_TER = 1;
    // ETM: trace all code, no branch broadcasting (the decoder takes direct branches from the ELF), no trigger
    ETM_LAR = 0xC5ACCE55;
    ETM_CR = 1u << 10;  // programming, powered
    while (!(ETM_SR & 1u << 1))
        ;
    if (CFG->etm == 1) {
        ETM_TRIGGER = 0x406F;    // never
        ETM_TEEVR = 0x6F;        // trace always enabled
        ETM_TECR1 = 0x01000000;  // exclude mode with no regions: all code
        ETM_FFLR = 24;           // stall the core below 24 free FIFO bytes
        ETM_TRACEIDR = 2;
        ETM_CR = ETM_CR_VAL | 1u << 10;
        ETM_CR = ETM_CR_VAL;     // end of programming, last
    } else
        ETM_CR = 1u;             // off
    CFG->etmidr = ETM_IDR;
    CFG->reinit++;
}

static int trace_ok(void)
{
    return ITM_TCR == ITM_TCR_VAL && ITM_TER == 1 && (DEMCR & 1u << 24) && (DBGMCU_CR & 1u << 5) &&
           (CFG->etm != 1 || (ETM_CR & (1u << 11 | 1u << 10 | 1u)) == 1u << 11);
}

void reset(void)
{
    RCC_AHB1ENR |= (1u << 3) | (1u << 4);  // GPIOD, GPIOE
    (void)RCC_AHB1ENR;
    REG(GPIOD + MODER) = (REG(GPIOD + MODER) & ~0x33000000u) | 0x11000000u;  // PD12, PD14
    if (CFG->magic != MAGIC) {  // after power-on; cfg survives a reset
        CFG->src = SRC_HSI;
        CFG->pll_n = 336;
        CFG->hdiv = 512;
        CFG->width = 4;
        CFG->burst = 16;
        CFG->idle = 64;
        CFG->seq = 0;
        CFG->n = 0;
        CFG->reinit = 0;
        CFG->ospeed = 3;
        CFG->etm = 1;
        CFG->magic = MAGIC;
    }
    apply_clock();
    apply_trace();
    CFG->applied = CFG->seq;

    uint32_t n = CFG->n, blink = 0;
    for (;;) {
        if (CFG->applied != CFG->seq) {
            uint32_t s = CFG->seq;
            apply_clock();
            apply_trace();
            CFG->applied = s;
        } else if (!trace_ok())
            apply_trace();
        for (uint32_t k = CFG->burst; k; k--) {
            uint32_t t = 0;
            while (!(ITM_STIM0 & 1u) && ++t < 100000)  // do not hang if ITM is off
                ;
            ITM_STIM0 = n++;
        }
        CFG->n = n;
        for (volatile uint32_t i = CFG->idle; i; i--)
            ;
        if (++blink >= 256) {
            blink = 0;
            REG(GPIOD + ODR) ^= 1u << 12;
        }
    }
}

extern uint32_t _estack;
__attribute__((section(".vectors"), used))
const void *vectors[2] = {&_estack, (void *)reset};
