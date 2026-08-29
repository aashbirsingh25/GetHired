import json
import os
import time
from datetime import datetime

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def load_dotenv(dotenv_path=None):
    if dotenv_path is None:
        dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(dotenv_path):
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

class LLMRouter:
    def __init__(self, filepath=CONFIG_FILE):
        self.filepath = filepath
        self.config = self._load()
        self.request_timestamps = {}
        # key_index -> unix time until which the key is cooling down after a
        # rate-limit/transient error (in-memory; resets on process restart)
        self.cooldowns = {}
        # key_index -> consecutive failure count, for escalating backoff
        self.consecutive_failures = {}

    def _load(self):
        load_dotenv()
        cfg = {}
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

        if "llm" not in cfg:
            cfg["llm"] = {}
        keys = cfg.get("llm", {}).get("keys", [])
        if not keys:
            keys = [
                {"provider": "gemini", "api_key": "YOUR_GEMINI_API_KEY", "quota_remaining": 1500, "used_today": 0},
                {"provider": "groq", "api_key": "YOUR_GROQ_API_KEY", "quota_remaining": 14400, "rpm_limit": 30, "used_today": 0},
                {"provider": "claude", "api_key": "YOUR_CLAUDE_API_KEY", "quota_remaining": 100000, "used_today": 0},
                {"provider": "openai", "api_key": "YOUR_OPENAI_API_KEY", "quota_remaining": 3000, "used_today": 0}
            ]

        # Gather environment variable keys
        env_gemini_keys = []
        if os.environ.get("GEMINI_API_KEYS"):
            env_gemini_keys = [k.strip() for k in os.environ["GEMINI_API_KEYS"].split(",") if k.strip()]
        elif os.environ.get("GEMINI_API_KEY"):
            env_gemini_keys = [os.environ["GEMINI_API_KEY"].strip()]
        else:
            i = 1
            while f"GEMINI_API_KEY_{i}" in os.environ:
                val = os.environ[f"GEMINI_API_KEY_{i}"].strip()
                if val:
                    env_gemini_keys.append(val)
                i += 1

        env_groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        env_claude_key = (os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        env_openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

        # Inject environment variable credentials into in-memory key objects
        if env_gemini_keys:
            gemini_indices = [idx for idx, k in enumerate(keys) if k.get("provider") == "gemini"]
            for i, k_val in enumerate(env_gemini_keys):
                if i < len(gemini_indices):
                    keys[gemini_indices[i]]["api_key"] = k_val
                else:
                    keys.append({"provider": "gemini", "api_key": k_val, "quota_remaining": 1500, "used_today": 0})
            # Drop gemini rows the env list no longer covers. Keys are injected
            # BY POSITION, so shrinking the env list (e.g. removing keys Google
            # permanently denied) used to leave trailing rows holding sanitized
            # "YOUR_*" placeholders - phantom keys that stayed in rotation and
            # burned a failed attempt every time they came up.
            stale = gemini_indices[len(env_gemini_keys):]
            if stale:
                for idx in sorted(stale, reverse=True):
                    keys.pop(idx)

        if env_groq_key:
            groq_item = next((k for k in keys if k.get("provider") == "groq"), None)
            if groq_item:
                groq_item["api_key"] = env_groq_key
            else:
                keys.append({"provider": "groq", "api_key": env_groq_key, "quota_remaining": 14400, "rpm_limit": 30, "used_today": 0})

        if env_claude_key:
            claude_item = next((k for k in keys if k.get("provider") == "claude"), None)
            if claude_item:
                claude_item["api_key"] = env_claude_key
            else:
                keys.append({"provider": "claude", "api_key": env_claude_key, "quota_remaining": 100000, "used_today": 0})

        if env_openai_key:
            openai_item = next((k for k in keys if k.get("provider") == "openai"), None)
            if openai_item:
                openai_item["api_key"] = env_openai_key
            else:
                keys.append({"provider": "openai", "api_key": env_openai_key, "quota_remaining": 3000, "used_today": 0})

        cfg["llm"]["keys"] = keys
        self._apply_daily_rollover(cfg)
        return cfg

    # Measured free-tier daily request caps (2026-08-29, observed live, not guessed).
    # gemini: 9 of 10 keys returned 429 ResourceExhausted after only 23-52 calls
    # each, so the per-key daily cap for gemini-3.5-flash is ~50 - NOT the 1500
    # the config previously assumed. Groq's cap is genuinely large, which makes
    # it the workhorse for bulk scoring.
    # groq additionally rate-limits per ORGANISATION on tokens/minute, so the
    # practical daily ceiling is far below its nominal request cap.
    DAILY_LIMITS = {"gemini": 50, "groq": 400, "claude": 100000, "openai": 3000}

    def _apply_daily_rollover(self, cfg):
        """Reset per-key daily counters when the provider's day changes.

        used_today only ever incremented, and quota_remaining only ever
        decremented, so once a key hit its cap it stayed 'exhausted' forever
        even though Google/Groq reset quotas every day. That silently shrank
        the usable key pool to nothing over time.

        The day boundary is midnight US-Pacific - Google's quota clock - which
        is 12:30 PM IST. Using the local (IST) date reset our counters 11
        hours early (harmless, cooldowns absorb the 429s) but also failed to
        reset them when the real refill landed mid-day, leaving fresh quota
        invisible until the next local midnight (observed 2026-08-29: keys
        refilled at 12:30 PM, config still showed them nearly spent at 2 PM).
        """
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        if cfg.get("llm", {}).get("quota_date") == today:
            return cfg
        for k in cfg.get("llm", {}).get("keys", []):
            limit = k.get("daily_limit") or self.DAILY_LIMITS.get(k.get("provider"), 1000)
            k["daily_limit"] = limit
            k["used_today"] = 0
            k["quota_remaining"] = limit
        cfg.setdefault("llm", {})["quota_date"] = today
        return cfg

    def _save(self):
        import copy
        safe_cfg = copy.deepcopy(self.config)
        for k in safe_cfg.get("llm", {}).get("keys", []):
            prov = str(k.get("provider", "LLM")).upper()
            k["api_key"] = f"YOUR_{prov}_API_KEY"
        from scan_coordinator import save_json
        save_json(self.filepath, safe_cfg)

    def get_best_available_key(self):
        """
        Returns tuple: (provider, api_key, key_index)

        Providers are tried in order of ACTUAL remaining daily headroom, not a
        fixed gemini-first order. The fixed order was actively harmful: gemini's
        free cap is ~50/key/day, so once those were spent every scoring call
        still tried all 10 gemini keys first, ate a 429 on each, and only then
        reached Groq - which had 14k calls available. Measured effect: quality
        refinement moved ~1 job per 100 seconds and mostly fell back to the
        local scorer.
        Applies RPM rate limit throttling if specified for key (e.g. 30/min for Groq).
        """
        self.config = self._load()
        keys = self.config.get("llm", {}).get("keys", [])
        now = time.time()

        # Rank providers by how much daily quota they actually have left.
        # Only count keys that are real (placeholder rows like YOUR_CLAUDE_API_KEY
        # would otherwise sort to the top on their notional 100k cap).
        headroom = {}
        for item in keys:
            prov = item.get("provider")
            key_val = (item.get("api_key") or "").strip()
            if not prov or not key_val or key_val.startswith("YOUR_"):
                continue
            headroom[prov] = headroom.get(prov, 0) + max(0, int(item.get("quota_remaining", 0) or 0))
        provider_order = sorted(headroom.keys(), key=lambda p: -headroom[p])

        for provider in provider_order:
            for idx, item in enumerate(keys):
                if item.get("provider") == provider:
                    key_val = (item.get("api_key") or "").strip()
                    quota = item.get("quota_remaining", 0)

                    if quota > 0 and key_val and not key_val.startswith("YOUR_"):
                        # Skip keys cooling down after rate-limit errors
                        if self.cooldowns.get(idx, 0) > now:
                            continue
                        # Check RPM ceiling if configured for key or provider
                        rpm_limit = item.get("rpm_limit")
                        if rpm_limit is None and provider == "groq":
                            rpm_limit = 30
                        if rpm_limit is None and provider == "gemini":
                            rpm_limit = 8  # free-tier Gemini keys are ~10 RPM; stay under

                        if rpm_limit is not None:
                            # Prune timestamps older than 60 seconds
                            timestamps = [t for t in self.request_timestamps.get(idx, []) if now - t < 60.0]
                            self.request_timestamps[idx] = timestamps
                            if len(timestamps) >= rpm_limit:
                                # Key is temporarily rate-limited in this 60s window, try next available key/tier
                                continue

                        return provider, key_val, idx

        return None, None, None

    def mark_used(self, provider: str, key_index: int):
        now = time.time()
        # a successful call clears the failure streak for this key
        self.consecutive_failures.pop(key_index, None)
        # Record timestamp for RPM throttling window
        if key_index not in self.request_timestamps:
            self.request_timestamps[key_index] = []
        self.request_timestamps[key_index].append(now)
        # Prune old timestamps
        self.request_timestamps[key_index] = [t for t in self.request_timestamps[key_index] if now - t < 60.0]

        keys = self.config.get("llm", {}).get("keys", [])
        if 0 <= key_index < len(keys):
            item = keys[key_index]
            if item.get("quota_remaining", 0) > 0:
                item["quota_remaining"] -= 1
            item["used_today"] = item.get("used_today", 0) + 1
            self._save()

    def on_quota_error(self, provider: str, key_index: int):
        keys = self.config.get("llm", {}).get("keys", [])
        if 0 <= key_index < len(keys):
            keys[key_index]["quota_remaining"] = 0
            self._save()

    def has_headroom(self, min_fraction: float = 0.30, provider: str = "gemini") -> bool:
        """True when the key pool still has at least `min_fraction` of its
        daily quota left.

        Used to give USER-FACING work (job scoring) priority over background
        work (company discovery, page learning): background callers check this
        first and skip the cycle when the pool is running low, so scoring never
        starves. Observed live: concurrent refinement + discovery caused 429s.
        """
        try:
            self.config = self._load()
            keys = [k for k in self.config.get("llm", {}).get("keys", [])
                    if k.get("provider") == provider
                    and not str(k.get("api_key", "")).startswith("YOUR_")]
            if not keys:
                return False
            total_remaining = sum(max(0, int(k.get("quota_remaining", 0) or 0)) for k in keys)
            total_capacity = sum(max(1, int(k.get("quota_remaining", 0) or 0)) + int(k.get("used_today", 0) or 0)
                                 for k in keys)
            if total_capacity <= 0:
                return False
            # Daily quota is rarely the real constraint - PER-MINUTE limits are.
            # Observed live: 265/18000 daily calls used, yet background work
            # still got 429s because the scoring pass was saturating RPM. So
            # also require that a majority of keys are NOT cooling down before
            # letting background work in.
            now = time.time()
            provider_keys = [(idx, k) for idx, k in enumerate(self.config.get("llm", {}).get("keys", []))
                             if k.get("provider") == provider
                             and not str(k.get("api_key", "")).startswith("YOUR_")]
            if not provider_keys:
                return False
            free = sum(1 for idx, _ in provider_keys if self.cooldowns.get(idx, 0) <= now)
            if free == 0 or (free / len(provider_keys)) < 0.5:
                return False
            return (total_remaining / total_capacity) >= min_fraction
        except Exception:
            return False

    def on_rate_limit(self, provider: str, key_index: int, cooldown_seconds: int = 120):
        """Transient failure (429/timeout/5xx): rest the key, don't kill it.

        Permanently zeroing quota on any exception (the old behavior) wiped
        the whole key pool within minutes of a burst - observed live with
        12 fresh keys all zeroed at ~6 calls each.

        Cooldowns ESCALATE per consecutive failure (2x each time, capped at
        1h). Without this, a key that times out on every call was retried
        every 60s and each attempt cost a ~60s API deadline - measured live:
        refinement crawled at ~1 job/100s because dead/slow keys sat at the
        front of the rotation.
        """
        fails = self.consecutive_failures.get(key_index, 0) + 1
        self.consecutive_failures[key_index] = fails
        backoff = min(cooldown_seconds * (2 ** (fails - 1)), 3600)
        self.cooldowns[key_index] = time.time() + backoff
        if fails >= 3:
            print(f"[LLMRouter] {provider} key #{key_index} failing repeatedly "
                  f"({fails}x) - cooling down {int(backoff)}s")

    def get_quota_status(self):
        self.config = self._load()
        keys = self.config.get("llm", {}).get("keys", [])
        status = {}
        now = time.time()
        for idx, k in enumerate(keys):
            prov = k.get("provider", "unknown")
            if prov not in status:
                status[prov] = []
            
            timestamps = [t for t in self.request_timestamps.get(idx, []) if now - t < 60.0]
            entry = {
                "key_index": idx,
                "api_key_masked": k.get("api_key", "")[:8] + "...",
                "quota_remaining": k.get("quota_remaining", 0),
                "used_today": k.get("used_today", 0)
            }
            if "rpm_limit" in k or prov == "groq":
                entry["rpm_limit"] = k.get("rpm_limit", 30)
                entry["rpm_used_last_60s"] = len(timestamps)
            status[prov].append(entry)
        return status

    def get_quota_headroom_info(self, current_hour: int = None) -> dict:
        if current_hour is None:
            from datetime import datetime
            current_hour = datetime.now().hour

        self.config = self._load()
        keys = self.config.get("llm", {}).get("keys", [])
        total_quota = sum(k.get("quota_remaining", 0) for k in keys if not (k.get("api_key") or "").startswith("YOUR_"))

        bg_config = self.config.get("background_search", {})
        interval_hours = max(1, bg_config.get("interval_hours", 3))

        remaining_hours_today = max(1, 24 - current_hour)
        remaining_cycles = max(1, (remaining_hours_today + interval_hours - 1) // interval_hours)

        avg_jobs_per_cycle = 15.0
        try:
            bg_log_file = os.path.join(os.path.dirname(self.filepath), "background_search_log.json")
            if os.path.exists(bg_log_file):
                mtime = os.path.getmtime(bg_log_file)
                if getattr(self, "_cached_bg_mtime", None) == mtime:
                    avg_jobs_per_cycle = self._cached_avg_jobs
                else:
                    with open(bg_log_file, "r", encoding="utf-8") as f:
                        bg_data = json.load(f)
                        cycles = bg_data.get("cycles", [])
                        if cycles:
                            counts = [c.get("jobs_after_filtering", c.get("raw_jobs_found", 15)) for c in cycles]
                            avg_jobs_per_cycle = sum(counts) / float(len(counts))
                    self._cached_bg_mtime = mtime
                    self._cached_avg_jobs = avg_jobs_per_cycle
        except Exception:
            pass

        try:
            from cycle_yield_tracker import CycleYieldTracker
            tracker = CycleYieldTracker()
            total_needed = 0.0
            for i in range(remaining_cycles):
                h = (current_hour + i * interval_hours) % 24
                mult = tracker.get_yield_multiplier(h)
                total_needed += avg_jobs_per_cycle * mult
        except Exception:
            total_needed = float(remaining_cycles * avg_jobs_per_cycle)

        total_needed = round(total_needed, 1)
        is_low = total_quota < total_needed

        if is_low:
            reasoning = f"LLM quota headroom low ({total_quota} remaining, {total_needed} estimated needed for {remaining_cycles} remaining cycles today, weighted by expected yield) - conserving paid-tier usage for highest-value cases only"
        else:
            reasoning = f"LLM quota headroom healthy ({total_quota} remaining, {total_needed} estimated needed for {remaining_cycles} remaining cycles today)"

        return {
            "total_quota_remaining": total_quota,
            "estimated_needed_today": total_needed,
            "remaining_cycles_today": remaining_cycles,
            "status": "low" if is_low else "healthy",
            "is_low": is_low,
            "reasoning": reasoning
        }


