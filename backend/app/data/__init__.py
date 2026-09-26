"""The data layer: what each source holds, how to read it, and what to hand back.

Everything here exists for one reason. An agent pointed at a database with nothing but
its tool schemas spends its first five calls rediscovering the schema, then guesses what
"revenue" means — and a guess about a metric is the one error that survives every check
downstream, because the arithmetic after it is perfectly correct.
"""
