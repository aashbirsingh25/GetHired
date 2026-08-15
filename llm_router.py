import json
import os
import time

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
        Searches providers in order: gemini -> groq -> claude -> openai
        Applies RPM rate limit throttling if specified for key (e.g. 30/min for Groq).
        """
        self.config = self._load()
        keys = self.config.get("llm", {}).get("keys", [])
        now = time.time()

        # Priority order: gemini -> groq -> claude -> openai
        for provider in ["gemini", "groq", "claude", "openai"]:
            for idx, item in enumerate(keys):
                if item.get("provider") == provider:
                    key_val = (item.get("api_key") or "").strip()
                    quota = item.get("quota_remaining", 0)

                    if quota > 0 and key_val and not key_val.startswith("YOUR_"):
                        # Check RPM ceiling if configured for key or provider
                        rpm_limit = item.get("rpm_limit")
                        if rpm_limit is None and provider == "groq":
                            rpm_limit = 30

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


