/* H2-only encoded E4M3FN layout preparation/finish. Common scalar RV64GC
 * only: no RVV, matrix, FP8/BF16 or Zbb instruction is required.
 */
#ifdef __riscv_vector
#error "FP8 control path requires scalar common-ISA flags"
#endif
#include "vme_fp8_split.h"
_Static_assert(sizeof(float) == 4, "FP32 output object ABI required");

typedef struct { uintptr_t begin, end; } span;

static int make_span(const void *pointer, size_t bytes, span *out) {
  uintptr_t begin = (uintptr_t)pointer;
  if (!pointer || !bytes || bytes > UINTPTR_MAX - begin) return 0;
  out->begin = begin; out->end = begin + bytes;
  return 1;
}
static int overlap(span a, span b) { return a.begin < b.end && b.begin < a.end; }
static int power2(size_t x) { return x && !(x & (x - 1)); }
static int dimensions(size_t m, size_t n, size_t k, size_t te,
                      size_t *a, size_t *b, size_t *c,
                      size_t *columns, size_t *blocks) {
  size_t cells, rows;
  if (!m || !n || !k || k > INTPTR_MAX || !power2(te) || te > SIZE_MAX / 2 ||
      __builtin_mul_overflow(m, k, a) || __builtin_mul_overflow(k, n, b) ||
      __builtin_mul_overflow(m, n, &cells) ||
      __builtin_mul_overflow(cells, sizeof(float), c)) return 0;
  size_t edge = te;
  rows = m / edge + (m % edge != 0);
  *columns = n / edge + (n % edge != 0);
  return !__builtin_mul_overflow(rows, *columns, blocks);
}

static int validate_buffers(const iris_vme_fp8_split_buffers *s,
                            const iris_vme_fp8_split_work *w,
                            size_t *a, size_t *b, size_t *c,
                            size_t *columns, size_t *blocks) {
  span spans[8];
  if (!s || !w || !s->sequence || !s->worker_mask || (s->worker_mask & ~3U) ||
      !dimensions(s->m, s->n, s->k, s->tile_edge, a, b, c, columns, blocks))
    return IRIS_VME_FP8_SPLIT_SHAPE;
  if (!power2(s->alignment_bytes) || s->alignment_bytes < sizeof(float) ||
      (uintptr_t)s->at % s->alignment_bytes ||
      (uintptr_t)s->bp % s->alignment_bytes ||
      (uintptr_t)s->cp % s->alignment_bytes ||
      (uintptr_t)s->output % sizeof(float)) return IRIS_VME_FP8_SPLIT_ALIGNMENT;
  if (s->a_bytes < *a || s->b_bytes < *b || s->at_bytes < *a ||
      s->bp_bytes < *b || s->cp_bytes < *c || s->output_bytes < *c)
    return IRIS_VME_FP8_SPLIT_CAPACITY;
  if (!make_span(s->a, *a, &spans[0]) || !make_span(s->b, *b, &spans[1]) ||
      !make_span(s->output, *c, &spans[2]) || !make_span(s->at, *a, &spans[3]) ||
      !make_span(s->bp, *b, &spans[4]) || !make_span(s->cp, *c, &spans[5]) ||
      !make_span(s, sizeof(*s), &spans[6]) || !make_span(w, sizeof(*w), &spans[7]))
    return IRIS_VME_FP8_SPLIT_SHAPE;
  /* Inputs may alias each other because both are read-only. Everything else
   * is distinct, including controller metadata and the canonical output. */
  for (unsigned i = 0; i < 8; ++i)
    for (unsigned j = i + 1; j < 8; ++j)
      if (!(i == 0 && j == 1) && overlap(spans[i], spans[j]))
        return IRIS_VME_FP8_SPLIT_ALIAS;
  return IRIS_VME_FP8_SPLIT_OK;
}

/* Byte layout copies preserve the actual input encodings. */
static void copy_bytes(unsigned char *to, const unsigned char *from, size_t bytes) {
  for (size_t i = 0; i < bytes; ++i) to[i] = from[i];
}

int iris_vme_fp8_split_prepare(const iris_vme_fp8_split_buffers *s,
                           iris_vme_fp8_split_work *w) {
  if (iris_vme_fp8_split_hart() != 2) return IRIS_VME_FP8_SPLIT_ROLE;
  size_t a, b, c, columns, blocks;
  int status = validate_buffers(s, w, &a, &b, &c, &columns, &blocks);
  if (status) return status;
  /* Saturn 536774bd... exu/fp/fp8ToE5M3.scala: E4M3 exp=in(6,3),
   * sig=in(2,0); NaN iff exp.andR && sig.andR, independently of sign. */
  for (size_t i = 0; i < a; ++i)
    if ((s->a[i] & 0x7fU) == 0x7fU) return IRIS_VME_FP8_SPLIT_NONFINITE;
  for (size_t i = 0; i < b; ++i)
    if ((s->b[i] & 0x7fU) == 0x7fU) return IRIS_VME_FP8_SPLIT_NONFINITE;
  for (size_t kk = 0; kk < s->k; ++kk) {

    for (size_t row = 0; row < s->m; ++row)
      s->at[kk * s->m + row] = s->a[row * s->k + kk];

  }
  copy_bytes((unsigned char *)s->bp, (const unsigned char *)s->b, b);
  for (size_t i = 0; i < c; ++i) ((unsigned char *)s->cp)[i] = 0;
#if defined(__riscv)
  __asm__ volatile("fence rw,rw" ::: "memory");
#endif
  *w = (iris_vme_fp8_split_work){s->at, s->bp, s->cp, s->m, s->n, s->k,
       s->tile_edge, s->alignment_bytes, columns, blocks, s->sequence, s->worker_mask, 1};
  return IRIS_VME_FP8_SPLIT_OK;
}

int iris_vme_fp8_split_validate_work(const iris_vme_fp8_split_work *w) {
  size_t a, b, c, columns, blocks;
  span spans[4];
  if (!w || !w->sequence || !w->worker_mask || (w->worker_mask & ~3U) ||
      w->finite_inputs_verified != 1 ||
      !dimensions(w->m, w->n, w->k, w->tile_edge, &a, &b, &c, &columns, &blocks) ||
      columns != w->block_columns || blocks != w->block_count)
    return IRIS_VME_FP8_SPLIT_SHAPE;
  if (!power2(w->alignment_bytes) || w->alignment_bytes < sizeof(float) ||
      (uintptr_t)w->at % w->alignment_bytes ||
      (uintptr_t)w->bp % w->alignment_bytes ||
      (uintptr_t)w->cp % w->alignment_bytes) return IRIS_VME_FP8_SPLIT_ALIGNMENT;
  if (!make_span(w->at, a, &spans[0]) || !make_span(w->bp, b, &spans[1]) ||
      !make_span(w->cp, c, &spans[2]) || !make_span(w, sizeof(*w), &spans[3]))
    return IRIS_VME_FP8_SPLIT_SHAPE;
  for (unsigned i = 0; i < 4; ++i)
    for (unsigned j = i + 1; j < 4; ++j)
      if (overlap(spans[i], spans[j])) return IRIS_VME_FP8_SPLIT_ALIAS;
  return IRIS_VME_FP8_SPLIT_OK;
}

int iris_vme_fp8_split_finish(const iris_vme_fp8_split_buffers *s,
                          const iris_vme_fp8_split_work *w) {
  if (iris_vme_fp8_split_hart() != 2) return IRIS_VME_FP8_SPLIT_ROLE;
  size_t a, b, c, columns, blocks;
  int status = validate_buffers(s, w, &a, &b, &c, &columns, &blocks);
  if (status) return status;
  status = iris_vme_fp8_split_validate_work(w);
  if (status) return status;
  if (w->at != s->at || w->bp != s->bp || w->cp != s->cp || w->m != s->m ||
      w->n != s->n || w->k != s->k || w->sequence != s->sequence ||
      w->worker_mask != s->worker_mask || w->tile_edge != s->tile_edge ||
      w->alignment_bytes != s->alignment_bytes) return IRIS_VME_FP8_SPLIT_CONFIG;
  copy_bytes((unsigned char *)s->output, (const unsigned char *)s->cp, c);
#if defined(__riscv)
  __asm__ volatile("fence rw,rw" ::: "memory");
#endif
  return IRIS_VME_FP8_SPLIT_OK;
}
