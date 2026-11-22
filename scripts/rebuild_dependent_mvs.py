"""DEPRECATED — use scripts/rebuild_mvs.py.

The base MVs (mv_financial_facts, mv_financial_facts_multidim) and their six
dependents now live as committed, period-aware DDL under scripts/ddl/mv/, applied
by scripts/rebuild_mvs.py. Previously this script only recreated the dependent MVs
and assumed the base MVs had been applied by hand from the design doc; that split
is gone. This shim delegates to the new runner so existing docs/commands still work.
"""
from rebuild_mvs import main

if __name__ == "__main__":
    print("[deprecated] scripts/rebuild_dependent_mvs.py -> delegating to scripts/rebuild_mvs.py\n")
    main()
