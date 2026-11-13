/*
 * OpenRouteAI - núcleo nativo de búsqueda de caminos (A*, Dijkstra, Lee, Hadlock).
 *
 * C99 "freestanding": no usa la biblioteca estándar ni reserva memoria; todos
 * los búferes los aporta Python (numpy).  Así se compila igual como .dll de
 * Windows (clang + lld-link, sin MSVC), .so de Linux o .dylib de macOS.
 *
 * Estado: s = (l*H + y)*W + x.  cost[s] >= ORA_INF => celda prohibida.
 * via_cost[y*W + x] >= ORA_INF => no se admite vía en esa posición.
 *
 * Licencia: GPL-3.0-or-later
 */
#if defined(_WIN32)
#define ORA_EXPORT __declspec(dllexport)
#else
#define ORA_EXPORT __attribute__((visibility("default")))
#endif

typedef unsigned char u8;

#define ORA_INF 1.0e30f
#define SQ2 1.41421356237f

#if defined(ORA_FREESTANDING)
/* clang puede emitir llamadas implícitas a memset/memcpy aunque no haya libc */
void *memset(void *d, int c, unsigned long long n) {
    u8 *p = (u8 *)d;
    while (n--) *p++ = (u8)c;
    return d;
}
void *memcpy(void *d, const void *s, unsigned long long n) {
    u8 *a = (u8 *)d;
    const u8 *b = (const u8 *)s;
    while (n--) *a++ = *b++;
    return d;
}
#endif

#if defined(_WIN32) && defined(ORA_FREESTANDING)
/* punto de entrada vacío para el enlace /noentry y símbolo que exige el CRT */
int _fltused = 0;
#endif

static const int DX[8] = {1, -1, 0, 0, 1, -1, 1, -1};
static const int DY[8] = {0, 0, 1, -1, 1, -1, -1, 1};
static const float LEN[8] = {1.f, 1.f, 1.f, 1.f, SQ2, SQ2, SQ2, SQ2};

typedef struct {
    int *heap;    /* estados ordenados por clave */
    float *key;   /* clave de cada estado (indexada por estado) */
    int *pos;     /* posición en el heap; -1 fuera, -2 cerrado */
    int n;
} Heap;

static void sift_up(Heap *h, int i) {
    int s = h->heap[i];
    float k = h->key[s];
    while (i > 0) {
        int p = (i - 1) >> 1;
        int ps = h->heap[p];
        if (h->key[ps] <= k) break;
        h->heap[i] = ps;
        h->pos[ps] = i;
        i = p;
    }
    h->heap[i] = s;
    h->pos[s] = i;
}

static void sift_down(Heap *h, int i) {
    int s = h->heap[i];
    float k = h->key[s];
    for (;;) {
        int c = 2 * i + 1;
        if (c >= h->n) break;
        if (c + 1 < h->n && h->key[h->heap[c + 1]] < h->key[h->heap[c]]) c++;
        int cs = h->heap[c];
        if (h->key[cs] >= k) break;
        h->heap[i] = cs;
        h->pos[cs] = i;
        i = c;
    }
    h->heap[i] = s;
    h->pos[s] = i;
}

static void push_or_decrease(Heap *h, int s, float k) {
    h->key[s] = k;
    if (h->pos[s] >= 0) {
        sift_up(h, h->pos[s]);
    } else {
        h->heap[h->n] = s;
        h->pos[s] = h->n;
        h->n++;
        sift_up(h, h->n - 1);
    }
}

static int pop_min(Heap *h) {
    int s = h->heap[0];
    h->n--;
    h->pos[s] = -2;
    if (h->n > 0) {
        h->heap[0] = h->heap[h->n];
        h->pos[h->heap[0]] = 0;
        sift_down(h, 0);
    }
    return s;
}

static inline int iabs(int a) { return a < 0 ? -a : a; }

/* distancia a la caja de destinos */
static inline void box_d(int x, int y, int tx0, int tx1, int ty0, int ty1, int *dx, int *dy) {
    *dx = x < tx0 ? tx0 - x : (x > tx1 ? x - tx1 : 0);
    *dy = y < ty0 ? ty0 - y : (y > ty1 ? y - ty1 : 0);
}

static inline float octile(int dx, int dy) {
    int mn = dx < dy ? dx : dy;
    return (float)iabs(dx - dy) + SQ2 * (float)mn;
}

/*
 * mode: 0 = A* (heurística octil), 1 = Dijkstra / maze, 2 = Lee (coste unitario),
 *       3 = Hadlock (detour number)
 *
 * Búferes de trabajo (tamaño N = L*W*H): g, key, parent, pos, heap, gen, pdir.
 * gen/curgen evitan reinicializar los búferes en cada llamada.
 *
 * Devuelve: >0 longitud del camino escrito en out (de origen a destino),
 *           -1 sin camino, -2 límite de expansiones, -3 out demasiado pequeño.
 */
ORA_EXPORT int ora_search(int W, int H, int L,
                          const float *cost, const float *via_cost, const u8 *tmask,
                          const int *src, int nsrc,
                          const float *layer_mult, float turn_penalty, int mode, long long max_exp,
                          float heur_weight,
                          int tx0, int tx1, int ty0, int ty1,
                          float *g, float *key, int *parent, int *pos, int *heap,
                          int *gen, int curgen, u8 *pdir,
                          int *out, int outcap, long long *expansions_out) {
    const int WH = W * H;
    Heap hp;
    hp.heap = heap;
    hp.key = key;
    hp.pos = pos;
    hp.n = 0;
    long long expansions = 0;
    int found = -1;
    /* desempate para Dijkstra/Lee/Hadlock: entre caminos de igual coste se
       prefiere el que avanza hacia el destino (eps*h < 0.5 no altera el óptimo
       con costes enteros) */
    const float eps = (mode == 0) ? 0.f : 0.5f / (float)(W + H + 2);
    /* A* ponderado: heur_weight > 1 acelera a costa de caminos algo más largos
       (como mucho heur_weight veces el óptimo) */
    const float hw = (mode == 0) ? (heur_weight > 0.f ? heur_weight : 1.f) : eps;

#define TOUCH(s)                 \
    if (gen[s] != curgen) {      \
        gen[s] = curgen;         \
        g[s] = ORA_INF;          \
        parent[s] = -1;          \
        pos[s] = -1;             \
        pdir[s] = 255;           \
    }

    for (int i = 0; i < nsrc; i++) {
        int s = src[i];
        if (s < 0 || s >= L * WH) continue;
        TOUCH(s);
        if (tmask[s]) {
            out[0] = s;
            if (expansions_out) *expansions_out = 0;
            return 1;
        }
        if (cost[s] >= ORA_INF) continue;
        g[s] = 0.f;
        int r = s % WH, y = r / W, x = r % W, dx, dy;
        box_d(x, y, tx0, tx1, ty0, ty1, &dx, &dy);
        float h = octile(dx, dy);
        push_or_decrease(&hp, s, hw * h);
    }

    while (hp.n > 0) {
        int s = pop_min(&hp);
        if (tmask[s]) {
            found = s;
            break;
        }
        if (++expansions > max_exp) {
            if (expansions_out) *expansions_out = expansions;
            return -2;
        }
        int l = s / WH, r = s % WH, y = r / W, x = r % W;
        int base = l * WH;
        float gs = g[s];
        int pd = pdir[s];
        int hd0 = 0;
        if (mode == 3) {
            int dx, dy;
            box_d(x, y, tx0, tx1, ty0, ty1, &dx, &dy);
            hd0 = dx > dy ? dx : dy;
        }
        const float *lm = layer_mult + 8 * l;
        for (int d = 0; d < 8; d++) {
            int nx = x + DX[d], ny = y + DY[d];
            if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
            int n = base + ny * W + nx;
            float c = cost[n];
            if (c >= ORA_INF) continue;
            if (DX[d] && DY[d]) {
                if (cost[base + y * W + nx] >= ORA_INF && cost[base + ny * W + x] >= ORA_INF) continue;
            }
            float ng;
            if (mode == 2) {
                ng = gs + 1.f;
            } else if (mode == 3) {
                int dx, dy;
                box_d(nx, ny, tx0, tx1, ty0, ty1, &dx, &dy);
                int hd = dx > dy ? dx : dy;
                ng = gs + (hd >= hd0 ? 1.f : 0.f);
            } else {
                ng = gs + LEN[d] * lm[d] * c;
                if (pd != 255 && pd != d) ng += turn_penalty;
            }
            TOUCH(n);
            if (ng < g[n]) {
                g[n] = ng;
                parent[n] = s;
                pdir[n] = (u8)d;
                int hx, hy;
                box_d(nx, ny, tx0, tx1, ty0, ty1, &hx, &hy);
                float h = octile(hx, hy);
                push_or_decrease(&hp, n, ng + hw * h);
            }
        }
        float vc = via_cost[r];
        if (vc < ORA_INF && L > 1) {
            float hv;
            {
                int dx, dy;
                box_d(x, y, tx0, tx1, ty0, ty1, &dx, &dy);
                hv = hw * octile(dx, dy);
            }
            for (int l2 = 0; l2 < L; l2++) {
                if (l2 == l) continue;
                int n = l2 * WH + r;
                float c = cost[n];
                if (c >= ORA_INF) continue;
                float ng = (mode == 2 || mode == 3) ? gs + 1.f : gs + vc + c;
                TOUCH(n);
                if (ng < g[n]) {
                    g[n] = ng;
                    parent[n] = s;
                    pdir[n] = 255;
                    push_or_decrease(&hp, n, ng + hv);
                }
            }
        }
    }
    if (expansions_out) *expansions_out = expansions;
    if (found < 0) return -1;

    /* reconstrucción: primero contar, luego escribir de origen a destino */
    int len = 0;
    for (int s = found; s >= 0; s = parent[s]) {
        len++;
        if (len > outcap) return -3;
    }
    int i = len - 1;
    for (int s = found; s >= 0; s = parent[s]) out[i--] = s;
    return len;
#undef TOUCH
}

ORA_EXPORT int ora_version(void) { return 2; }
