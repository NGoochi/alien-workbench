#! python 3
# NODE_INPUTS: a:float, b:float
# NODE_OUTPUTS: sum, product, log

# ─── DEFAULTS ─────────────────────────────────────────────────────────
if a is None: a = 0.0
if b is None: b = 0.0

# ─── PROCESSING ───────────────────────────────────────────────────────
sum = a + b
product = a * b

# ─── OUTPUT ───────────────────────────────────────────────────────────
log = f"a={a}, b={b} → sum={sum}, product={product}"
