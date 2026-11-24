# ApexHunter 2.0 — Canonical Project Rules & AI Agent Protocols

> **Scope:** Model-agnostic operating protocol for all AI assistants, agents, and engineers touching the `RyzEnHunTer/apexhunter` repository.  
> **Status:** MANDATORY & ENFORCED

---

## 1. Core Operating Protocol

Every enhancement, bug fix, or refactoring MUST follow the 5-step GSD cycle:

$$\mathbf{SPEC} \longrightarrow \mathbf{PLAN} \longrightarrow \mathbf{EXECUTE} \longrightarrow \mathbf{VERIFY} \longrightarrow \mathbf{COMMIT}$$

1. **SPEC:** Clarify requirements and mathematical bounds before writing code.
2. **PLAN:** Decompose multi-step tasks into atomic units.
3. **EXECUTE:** Implement minimal surgical diffs adhering to immutability and PEP 8 standards.
4. **VERIFY:** Execute test suite (`python -m unittest discover lk_system/tests`) and capture empirical proof.
5. **COMMIT:** Atomic Conventional Commits format: `<type>(<scope>): <description>`.

---

## 2. Invariants & Non-Negotiable Architecture Rules

1. **Anti-Repaint Guarantee:**
   - Strategy logic MUST evaluate only on closed candles (`rates[-2]`).
   - Intra-bar tick evaluation is strictly prohibited for entry generation.
2. **Zero Curve-Fitting Policy:**
   - Parameter adjustments must apply symmetrically across multi-month datasets. No ad-hoc parameter tweaks tailored to single-day or single-week anomalies.
3. **Dual Circuit Breaker Integrity:**
   - Never disable or bypass `RiskGuardian.is_symbol_trading_allowed()` or `calculate_fitted_risk_usd()`.
   - The 00:00 server-time anchor reset logic must remain intact.
4. **Decoupled Asynchronous Telemetry:**
   - Never make blocking HTTP requests (e.g. `requests.post()` to Telegram or Discord) in the primary surveillance loop. All notifications must route through `notifier._enqueue()`.
5. **Atomic State File I/O:**
   - Never write directly to `bot_runtime_state.json`. Always write to a temporary file in the same directory and perform an atomic `os.replace()` to prevent JSON corruption during power outages or process kills.
6. **Dynamic Asset Handling:**
   - Never hardcode raw broker symbol strings (e.g. `XAUUSD.m`) in core strategy modules. Always route through `AssetHandler.resolve_all_symbols()` and query specs through `bridge.get_specs()`.

---

## 3. Search-First & Minimal Read Discipline

Before reading any source file:
1. Use targeted search (`Select-String` or grep) to identify specific functions or lines.
2. Read only targeted line ranges to avoid context clutter.
3. Never guess or hallucinate parameters; verify against `PROJECT_DOCUMENTATION.md` or unit tests.

---

## 4. Test Verification Contract

- Any code modification touching `core/`, `live/`, or `backtest/` MUST be verified by running:
  ```powershell
  python -m unittest discover lk_system/tests
  ```
- Minimum pass rate: **100% (Zero failing tests permitted)**.
