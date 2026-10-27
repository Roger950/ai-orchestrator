"""Evidence-based model routing; capability scores are local heuristics, not benchmarks."""
import json
import re
from datetime import datetime, timezone

from .browser import controls, identify, tool_text
from .core import MODELS, json_write, now, safe_id

STATES = {"discovered", "available", "unavailable", "quota_exhausted", "unknown"}
GATED = re.compile(r"upgrade|subscribe|purchase|payment|pro required|requires? pro|升级|订阅|购买|付费|需要.*pro", re.I)
QUOTA = re.compile(r"quota|limit reached|out of messages|usage limit|额度|次数.*用完|达到.*上限", re.I)
MODEL_NAME = re.compile(r"(?:GPT[- ]\d|Claude\s+[A-Za-z]+\s+\d|(?:Opus|Sonnet|Haiku|Fable)\s+\d|Gemini\s+\d)", re.I)


def profile(name, levels, quality, reasoning, medicine, long_text, code, speed, cost):
    return {"name": name, "reasoning_levels": levels, "capabilities": {
        "quality": quality, "reasoning": reasoning, "medicine": medicine,
        "long_text": long_text, "code": code, "speed": speed, "quota_economy": cost},
        "capability_basis": "示例候选名单与可调整的本地启发式评分；未经性能基准验证",
        "exists": None, "state": "unknown", "account_access": "unknown", "enabled": False,
        "updated_at": now(), "verified_at": None, "evidence": None}


def default_policy():
    return {"schema_version": 1, "updated_at": now(), "verification_ttl_seconds": 1800,
        "never_purchase_or_upgrade": True,
        "platforms": {
            "chatgpt": {"subscription": "unknown", "subscription_source": "not_configured",
                "allowed_models": None, "automatic_switch_allowed": False,
                "models": [profile("GPT-6", ["High", "Medium"], 5, 5, 5, 4, 5, 2, 2)]},
            "claude": {"subscription": "unknown", "subscription_source": "not_configured",
                "allowed_models": ["Claude Sonnet 5.5"], "automatic_switch_allowed": False,
                "models": [profile("Claude Sonnet 5.5", ["Default"], 4, 4, 4, 5, 5, 4, 4),
                           profile("Claude Opus 5.5", ["Default"], 5, 5, 5, 5, 5, 1, 1)]},
            "gemini": {"subscription": "unknown", "subscription_source": "unconfirmed",
                "allowed_models": None, "automatic_switch_allowed": False,
                "models": [profile("Gemini 4 Argon", ["Default"], 5, 5, 4, 5, 4, 3, 3)]}},
        "locks": {}, "pending_switch_confirmations": {}}


def canonical(label):
    label = re.sub(r"^(?:Model selector(?:, current model is)?|Current model|模型选择器|当前模型|模型|Selected)\s*[:：,]?\s*", "", label, flags=re.I)
    label = re.split(r"\s+(?:—|\||Requires|Upgrade|需要|升级)", label, maxsplit=1, flags=re.I)[0].strip()
    if re.match(r"^(?:Sonnet|Opus|Haiku|Fable)\s+\d", label, re.I):
        label = "Claude " + label
    claude = re.match(r"Claude\s+([A-Za-z]+)\s+(\d+(?:\.\d+)*)", label, re.I)
    if claude:
        return claude.group(0)
    gemini = re.match(r"(?:Gemini\s+)?(\d+(?:\.\d+)*)\s+([A-Za-z][\w.-]*)", label, re.I)
    if gemini:
        return "Gemini " + gemini.group(1) + " " + gemini.group(2)
    # Reasoning is a separate dimension, never part of the model identity.
    return re.sub(r"\s+(High|Medium|Low|Default)$", "", label, flags=re.I).strip()


def selector_evidence(snapshot):
    """Only accessible controls/alerts; no chat body, DOM dump or network access."""
    result = {"models": [], "selector": [], "submenus": [], "reasoning_controls": [], "actual": None,
              "reasoning_level": None, "quota": False, "gated": False,
              "composer": len(controls(snapshot)["inputs"]) == 1}
    selected = []
    for line in snapshot.splitlines():
        m = re.search(r'uid=(\S+)\s+(button|combobox|menuitemradio|menuitem|option|radio|alert|dialog|status)\s+"([^"]*)"(.*)', line)
        if not m:
            continue
        uid, role, label, flags = m.groups()
        if role in {"alert", "dialog", "status"}:
            result["quota"] |= bool(QUOTA.search(label))
            result["gated"] |= bool(GATED.search(label))
            continue
        if role in {"button", "combobox"} and re.search(r"model selector|choose model|select model|mode picker|模型选择|选择.*模型|current model|当前模型|^模型：", label, re.I):
            result["selector"].append({"uid": uid, "label": label, "expanded": "expanded" in flags})
        if role == "menuitem" and label in {"选择模型", "Choose model", "Select model"}:
            result["submenus"].append({"uid": uid, "label": label})
        cn_level = re.search(r"(?:强度|思考强度)\s*(高|中等|中|低)", label + " " + flags)
        if cn_level:
            result["reasoning_level"] = {"高": "High", "中等": "Medium", "中": "Medium", "低": "Low"}[cn_level.group(1)]
        if role in {"button", "radio", "option", "menuitemradio"} and label in {"High", "Medium", "Low", "Default"}:
            result["reasoning_controls"].append({"uid": uid, "level": label})
            if re.search(r"(?:checked|selected)(?:=true|\b)(?![=]false)", flags):
                result["reasoning_level"] = label
        if role == "combobox" and re.search(r"reasoning|推理", label, re.I):
            value = re.search(r'value="(High|Medium|Low|Default)"', flags)
            if value:
                result["reasoning_level"] = value.group(1)
        name = canonical(label)
        if not MODEL_NAME.match(name):
            continue
        blocked = bool(GATED.search(label)) or "disabled" in flags
        chosen = bool(re.search(r"(?:checked|selected)(?:=true|\b)(?![=]false)", flags))
        # Closed selectors showing the model name are current-model evidence.
        chosen |= role == "button" and bool(re.search(r"current model|model selector|当前模型|^模型：", label, re.I))
        chosen |= label.startswith("Selected ")
        if role == "button" and "haspopup" in flags and not blocked:
            chosen = True
            if not result["selector"]:
                result["selector"].append({"uid": uid, "label": label, "expanded": "expanded" in flags})
        level = re.search(r"\s+(High|Medium|Low|Default|中等|高|低)$", label, re.I)
        level_value = ({"中等": "Medium", "高": "High", "低": "Low"}.get(level.group(1), level.group(1).title()) if level else None)
        record = {"name": name, "uid": uid, "blocked": blocked, "selected": chosen,
                  "label": label, "reasoning_level": level_value}
        result["models"].append(record)
        if chosen:
            selected.append(record)
    unique = {r["name"] for r in selected}
    if len(unique) == 1:
        result["actual"] = selected[0]["name"]
        result["reasoning_level"] = result["reasoning_level"] or selected[0]["reasoning_level"]
    return result


class ModelRouter:
    def __init__(self, root, call_tool=None, policy_path=None, fixture_url=None):
        self.root = root.resolve()
        self.path = policy_path or self.root / "config" / "model-policy.json"
        if not self.path.resolve().is_relative_to(self.root):
            raise ValueError("Policy must be inside the project")
        self.call_tool = call_tool
        self.fixture_url = fixture_url
        # Synthetic evidence MUST NOT enter the real-account policy file.
        if fixture_url and self.path == self.root / "config" / "model-policy.json":
            raise ValueError("Local fixtures require a separate policy under logs")
        if not self.path.exists():
            json_write(self.path, default_policy())

    def load(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data):
        data["updated_at"] = now()
        json_write(self.path, data)

    def connection_lost(self, message):
        """Record a proven transport outage without changing any browser settings."""
        data = self.load()
        data["connection"] = {"status": "unavailable", "at": now(), "reason": message}
        for pp in data["platforms"].values():
            for model in pp["models"]:
                if model.get("verified_at"):
                    model["last_verified_at"] = model["verified_at"]
                model.update(enabled=False, verified_at=None)
                if model["state"] not in {"unavailable", "quota_exhausted"}:
                    model.update(state="unknown", account_access="unknown")
        for locks in data["locks"].values():
            for lock in locks.values():
                if lock.get("verified_at"):
                    lock["last_verified_at"] = lock["verified_at"]
                lock.update(lock_status="unknown", verified_at=None, exception="Chrome MCP 连接已断开；需恢复后重新验证")
        self.save(data)
        for task_id, locks in data["locks"].items():
            for lock in locks.values():
                self.audit(task_id, lock)

    def call(self, name, **args):
        return tool_text(self.call_tool(name, args))

    @staticmethod
    def permitted(platform_policy, model):
        allowed = platform_policy["allowed_models"]
        return allowed is None or model in allowed

    def observe(self, platform, page, open_selector=False):
        try:
            return self._observe(platform, page, open_selector)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            data = self.load()
            if platform in data["platforms"]:
                pp = data["platforms"][platform]
                pp["last_error"] = {"at": now(), "message": str(error), "state": "unknown"}
                for model in pp["models"]:
                    model.update(enabled=False, verified_at=None)
                    if model["state"] not in {"unavailable", "quota_exhausted"}:
                        model.update(state="unknown", account_access="unknown")
                for locks in data["locks"].values():
                    if platform in locks:
                        locks[platform].update(lock_status="unknown", exception="无法重新读取页面模型选择器")
                self.save(data)
                for task_id, locks in data["locks"].items():
                    if platform in locks:
                        self.audit(task_id, locks[platform])
            raise

    def _observe(self, platform, page, open_selector=False):
        if platform not in MODELS or identify(page["url"], self.fixture_url) != platform:
            raise ValueError("Platform/page mismatch")
        if self.fixture_url and not page["url"].startswith("file:"):
            raise ValueError("Fixture mode cannot inspect real pages")
        evidence = selector_evidence(self.call("take_snapshot", pageId=page["pageId"], verbose=False))
        if open_selector and len(evidence["selector"]) == 1 and not evidence["gated"] and not evidence["quota"]:
            if not evidence["selector"][0]["expanded"]:
                self.call("click", pageId=page["pageId"], uid=evidence["selector"][0]["uid"])
                evidence = selector_evidence(self.call("take_snapshot", pageId=page["pageId"], verbose=False))
            if len(evidence["submenus"]) == 1:
                prior_level = evidence["reasoning_level"]
                self.call("click", pageId=page["pageId"], uid=evidence["submenus"][0]["uid"])
                evidence = selector_evidence(self.call("take_snapshot", pageId=page["pageId"], verbose=False))
                evidence["reasoning_level"] = evidence["reasoning_level"] or prior_level
        data = self.load()
        data["connection"] = {"status": "connected", "at": now(), "reason": "成功读取可见选择器快照"}
        pp = data["platforms"][platform]
        seen = {r["name"]: r for r in evidence["models"]}
        for name in seen:
            if not any(r["name"] == name for r in pp["models"]):
                pp["models"].append(profile(name, [], 2, 2, 2, 2, 2, 2, 2))
        for model in pp["models"]:
            observed = seen.get(model["name"])
            prior = dict(model)
            prior_state = model["state"]
            model.update(updated_at=now(), enabled=False, verified_at=None)
            model["account_access"] = "unknown"
            if not self.permitted(pp, model["name"]):
                model.update(state="unavailable", account_access="policy_denied")
                if observed:
                    model["exists"] = True
                continue
            if prior_state == "quota_exhausted" and pp.get("requires_switch_confirmation"):
                model.update(state="quota_exhausted", account_access="quota_exhausted")
                continue
            if not observed:
                model["state"] = "unknown"  # absence from one menu is not proof of non-existence
                continue
            model["exists"] = True
            model["evidence"] = {"pageId": page["pageId"], "url": page["url"],
                                 "label": observed["label"], "synthetic": bool(self.fixture_url)}
            model["state"] = "discovered"
            if not self.permitted(pp, model["name"]) or observed["blocked"] or evidence["gated"]:
                model.update(state="unavailable", account_access="denied")
            elif evidence["quota"]:
                model.update(state="quota_exhausted", account_access="quota_exhausted")
            elif model["name"] == evidence["actual"] and evidence["composer"]:
                model.update(state="available", account_access="verified_visible_selection", enabled=True,
                             verified_at=now(), observed_reasoning=evidence["reasoning_level"])
                if evidence["reasoning_level"] and evidence["reasoning_level"] not in model["reasoning_levels"]:
                    model["reasoning_levels"].append(evidence["reasoning_level"])
            elif (model["name"] != evidence["actual"] and evidence["composer"] and
                  prior_state == "available" and prior.get("verified_at") and
                  (prior.get("evidence") or {}).get("pageId") == page["pageId"] and
                  (datetime.now(timezone.utc) - datetime.fromisoformat(prior["verified_at"])).total_seconds() < data["verification_ttl_seconds"]):
                # A recent, separately verified model remains rankable while the
                # same page still lists it without a gate. Merely listing a new model never grants access.
                model.update(state="available", account_access=prior["account_access"], enabled=True,
                             verified_at=prior["verified_at"], observed_reasoning=prior.get("observed_reasoning"))
        pp["last_observation"] = {"page": page, "actual": evidence["actual"],
            "reasoning_level": evidence["reasoning_level"], "quota": evidence["quota"],
            "gated": evidence["gated"], "at": now(), "synthetic": bool(self.fixture_url)}
        # Quota remains a stop condition until a human explicitly confirms another choice.
        if evidence["quota"]:
            pp["requires_switch_confirmation"] = True
        for locks in data["locks"].values():
            lock = locks.get(platform)
            if lock:
                if evidence["quota"]:
                    lock.update(lock_status="quota_exhausted", exception="额度耗尽，禁止自动降级")
                elif evidence["gated"]:
                    lock.update(lock_status="unavailable", exception="升级/权限提示，禁止支付或重试")
                elif evidence["actual"] != lock["model"] or evidence["reasoning_level"] != lock["reasoning_level"]:
                    lock.update(lock_status="unknown", exception="锁定与可见型号/推理等级不一致")
                else:
                    actual = next((m for m in pp["models"] if m["name"] == lock["model"]), {})
                    if actual.get("state") != "available":
                        lock.update(lock_status="unknown", exception="账号可用性尚未确认")
        self.save(data)
        for task_id, locks in data["locks"].items():
            if platform in locks:
                self.audit(task_id, locks[platform])
        return evidence

    def rank(self, platform, task_type="structured", priority="balanced"):
        if task_type not in {"reasoning", "medical", "long_text", "code", "structured"} or priority not in {"quality", "balanced", "speed", "quota"}:
            raise ValueError("Unsupported routing preference")
        data = self.load()
        pp = data["platforms"][platform]
        if pp.get("requires_switch_confirmation"):
            return {"status": "quota_exhausted", "ranked": [], "reason": "必须先由用户确认切换；不自动降级"}
        weights = {"quality": 1, "speed": 2, "quota_economy": 1}
        weights.update({"reasoning": 4, "medicine": 5, "quality": 3} if task_type == "medical" else
                       {"reasoning": 5, "quality": 3} if task_type == "reasoning" else
                       {"long_text": 5} if task_type == "long_text" else
                       {"code": 5} if task_type == "code" else {"speed": 5, "quota_economy": 3})
        if priority in {"quality", "speed", "quota"}:
            key = "quota_economy" if priority == "quota" else priority
            weights[key] += 4
        ranked = []
        for model in pp["models"]:
            if not self.permitted(pp, model["name"]) or model["state"] != "available" or not model["enabled"] or not model.get("verified_at"):
                continue
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(model["verified_at"])).total_seconds()
            if age > data["verification_ttl_seconds"]:
                continue
            levels = model["reasoning_levels"]
            preferred = "High" if task_type in {"medical", "reasoning"} else "Medium"
            # A desired level is not evidence that it exists or is selected.
            level = preferred if preferred in levels else model.get("observed_reasoning")
            ranked.append({"model": model["name"], "reasoning_level": level,
                           "score": sum(model["capabilities"].get(k, 0) * v for k, v in weights.items()),
                           "reason": f"账号权限已过滤；任务={task_type}；偏好={priority}；本地启发式权重={weights}"})
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return {"status": "ranked" if ranked else "unknown", "ranked": ranked,
                "reason": "缺乏当前账号可用性证据时暂停，不根据候选名单推断权限"}

    def lock(self, task_id, platform, model, level, page, reviewer=None):
        safe_id(task_id)
        self.observe(platform, page)
        data = self.load()
        pp = data["platforms"][platform]
        obs = pp["last_observation"]
        found = next((m for m in pp["models"] if m["name"] == model), {})
        previous = data["locks"].get(task_id, {}).get(platform)
        confirmation = data["pending_switch_confirmations"].get(task_id, {}).get(platform)
        previous_model = previous.get("prior_verified_model") if previous else None
        previous_level = previous.get("prior_verified_level") if previous else None
        changing = bool(previous_model and (previous_model != model or previous_level != level))
        consent = confirmation == {"model": model, "reasoning_level": level}
        status, exception = "unknown", None
        if not self.permitted(pp, model) or found.get("state") == "unavailable":
            status, exception = "unavailable", "账号策略禁止或网页要求升级"
        elif pp.get("requires_switch_confirmation") or found.get("state") == "quota_exhausted":
            status, exception = "quota_exhausted", "额度耗尽"
        elif changing and not consent:
            exception = "改变已锁定型号：需要实际用户确认，不能静默降级"
        elif (level is not None and obs["reasoning_level"] is not None and
              found.get("state") == "available" and obs["actual"] == model and obs["reasoning_level"] == level):
            status = "locked"
        else:
            exception = "网页尚未确认目标型号与推理等级；请人工选择后重新验证"
        lock = {"platform": platform, "model": model, "target_model": model,
                "actual_confirmed_model": obs["actual"], "reasoning_level": level,
                "lock_status": status, "verified_at": now() if status == "locked" else None,
                "observed_at": obs["at"], "page": page, "exception": exception,
                "reason": "可见模型选择器、输入框可用性、账号允许名单和推理等级联合检查",
                "synthetic": bool(self.fixture_url),
                "prior_verified_model": model if status == "locked" else previous_model,
                "prior_verified_level": level if status == "locked" else previous_level}
        data["locks"].setdefault(task_id, {})[platform] = lock
        if consent and status == "locked":
            del data["pending_switch_confirmations"][task_id][platform]
        self.save(data)
        self.audit(task_id, lock)
        return lock

    def confirm_switch(self, task_id, platform, model, level, reviewer):
        if not reviewer.strip():
            raise ValueError("必须记录实际用户确认者")
        data = self.load()
        pp = data["platforms"][platform]
        if not self.permitted(pp, model):
            raise ValueError("当前账号配置禁止此模型；普通切换确认不能扩大账号允许名单")
        pp["requires_switch_confirmation"] = False
        data["pending_switch_confirmations"].setdefault(safe_id(task_id), {})[platform] = {
            "model": model, "reasoning_level": level}
        self.save(data)
        self.audit(task_id, {"platform": platform, "target_model": model, "reasoning_level": level,
                             "event": "user_confirmed_switch", "reviewer": reviewer, "at": now()})

    def switch(self, task_id, platform, model, level, page):
        evidence = self.observe(platform, page, open_selector=True)
        data = self.load()
        pp = data["platforms"][platform]
        local = bool(self.fixture_url and page["url"].startswith(self.fixture_url + "?"))
        allowed = local or pp["automatic_switch_allowed"]
        if not self.permitted(pp, model) or evidence["gated"] or evidence["quota"]:
            return self.lock(task_id, platform, model, level, page)
        if not allowed:
            result = self.lock(task_id, platform, model, level, page)
            result["manual_instruction"] = f"请在 {platform} 网页选择 {model} / {level}，不要点击升级或付费，然后运行 router-lock 验证。"
            self.audit(task_id, result)
            return result
        previous = data["locks"].get(task_id, {}).get(platform)
        consent = data["pending_switch_confirmations"].get(task_id, {}).get(platform)
        changing = bool(previous and previous.get("prior_verified_model") and
                        (previous["prior_verified_model"] != model or previous.get("prior_verified_level") != level))
        if pp.get("requires_switch_confirmation") or (changing and consent != {"model": model, "reasoning_level": level}):
            raise ValueError("切换锁定模型前必须取得用户确认")
        if evidence["actual"] != model:
            options = [r for r in evidence["models"] if r["name"] == model and not r["blocked"]]
            if len(options) != 1:
                return self.lock(task_id, platform, model, level, page)
            self.call("click", pageId=page["pageId"], uid=options[0]["uid"])
            evidence = self.observe(platform, page)
        if evidence["gated"] or evidence["quota"]:
            return self.lock(task_id, platform, model, level, page)
        if evidence["reasoning_level"] != level:
            levels = [r for r in evidence["reasoning_controls"] if r["level"] == level]
            if len(levels) == 1:
                self.call("click", pageId=page["pageId"], uid=levels[0]["uid"])
        return self.lock(task_id, platform, model, level, page)

    def require_lock(self, task_id, platform):
        lock = self.load()["locks"].get(task_id, {}).get(platform)
        if not lock or lock["lock_status"] != "locked":
            raise ValueError("Model Router 未锁定可用模型，暂停提交")
        current = self.lock(task_id, platform, lock["model"], lock["reasoning_level"], lock["page"])
        if current["lock_status"] != "locked":
            raise ValueError("Model Router 锁定不一致/权限未知，暂停提交")
        return current

    def plan(self, task_id, description, task_type=None, priority="balanced"):
        if task_type is None:
            task_type = ("medical" if re.search(r"医学|临床|文献|肿瘤|耐药|medical|clinical", description, re.I) else
                         "code" if re.search(r"代码|编程|开发|code|software", description, re.I) else
                         "long_text" if re.search(r"长文|长文本|long.text", description, re.I) else
                         "reasoning" if re.search(r"复杂推理|证明|推导|reasoning", description, re.I) else "structured")
        plans = {p: self.rank(p, task_type, priority) for p in MODELS}
        output = {"task_type": task_type, "priority": priority, "at": now(), "platforms": plans,
                  "note": "推荐不是锁定，也不是提交授权；未知模型不得静默启用"}
        json_write(self.root / "tasks" / safe_id(task_id) / "routing-plan.json", output)
        return output

    def report(self):
        data = self.load()
        lines = ["# Model Router 状态报告", "", f"更新时间：{data['updated_at']}", "",
                 "账号套餐来自用户说明；available 仅表示最近一次可见选择器及输入框证据。"
                 "未发送测试消息验证服务端额度，提交前必须再次验证锁定。", "",
                 "| 平台 | 账号约束 | 模型 | 存在证据 | 账号可用状态 | 推理等级 |", "|---|---|---|---|---|---|"]
        connection = data.get("connection")
        if connection:
            lines[4:4] = [f"Chrome MCP 连接：{connection['status']}；{connection.get('reason', '')}。", ""]
        for platform, pp in data["platforms"].items():
            for model in pp["models"]:
                exists = "历史网页已发现" if model["exists"] else "未确认"
                lines.append(f"| {platform} | {pp['subscription']}（{pp['subscription_source']}） | {model['name']} | {exists} | {model['state']} | {model.get('observed_reasoning') or '未确认'} |")
        lines += ["", "Claude 仅允许 Claude Sonnet 5.5；其他型号即使存在也不得自动选择。",
                  "新型号只被列出时为 discovered；只有可见选中、输入可用且没有权限/额度警告时才可验证为 available。",
                  "推理档位未显示时保持未知；不能把候选档位当作当前界面已确认事实。",
                  "真实平台自动切换默认关闭；只自动打开模型菜单读取选项，人工切换后验证锁定。",
                  "没有购买、支付、升级、Cookie、令牌或账号配置修改功能。"]
        lines += ["", "## 当前任务锁定", "", "| 任务 | 平台 | 目标型号 | 实际确认型号 | 推理等级 | 锁定状态 |", "|---|---|---|---|---|---|"]
        for task_id, locks in data["locks"].items():
            for platform, lock in locks.items():
                lines.append(f"| {task_id} | {platform} | {lock['model']} | {lock['actual_confirmed_model'] or '未确认'} | {lock['reasoning_level'] or '未确认'} | {lock['lock_status']} |")
        path = self.root / "logs" / "model-status.md"
        from .core import write
        write(path, "\n".join(lines) + "\n")
        return path

    def audit(self, task_id, event):
        path = self.root / "logs" / safe_id(task_id) / "model-router.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")
        if (self.root / "tasks" / task_id).exists():
            json_write(self.root / "tasks" / task_id / "routing.json", self.load()["locks"].get(task_id, {}))
