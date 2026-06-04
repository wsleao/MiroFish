diff --git a/frontend/src/api/simulation.js b/frontend/src/api/simulation.js
index f878586..0000000 100644
--- a/frontend/src/api/simulation.js
+++ b/frontend/src/api/simulation.js
@@ -6,8 +6,14 @@ import service, { requestWithRetry } from './index'
  * @param {Object} data - { project_id, graph_id?, enable_twitter?, enable_reddit? }
  */
 export const createSimulation = (data) => {
-  return requestWithRetry(() => service.post('/api/simulation/create', data), 3, 1000)
+  const payload = {
+    ...data,
+    enable_twitter: false,
+    enable_reddit: false
+  }
+  return requestWithRetry(() => service.post('/api/simulation/create', payload), 3, 1000)
 }

@@ -37,19 +43,22 @@ export const getSimulation = (simulationId) => {
 /**
  * 获取模拟的 Agent Profiles
  * @param {string} simulationId
- * @param {string} platform - 'reddit' | 'twitter'
+ * @param {string|null} platform - optional platform. Omit for internal/fallback mode.
  */
-export const getSimulationProfiles = (simulationId, platform = 'reddit') => {
-  return service.get(`/api/simulation/${simulationId}/profiles`, { params: { platform } })
+export const getSimulationProfiles = (simulationId, platform = null) => {
+  const options = platform ? { params: { platform } } : undefined
+  return service.get(`/api/simulation/${simulationId}/profiles`, options)
 }

 /**
  * 实时获取生成中的 Agent Profiles
  * @param {string} simulationId
- * @param {string} platform - 'reddit' | 'twitter'
+ * @param {string|null} platform - ignored by default to avoid hardcoded reddit polling.
  */
-export const getSimulationProfilesRealtime = (simulationId, platform = 'reddit') => {
-  return service.get(`/api/simulation/${simulationId}/profiles/realtime`, { params: { platform } })
+export const getSimulationProfilesRealtime = (simulationId, platform = null) => {
+  return service.get(`/api/simulation/${simulationId}/profiles/realtime`)
 }

@@ -113,11 +122,11 @@ export const getRunStatusDetail = (simulationId) => {
 /**
  * 获取模拟中的帖子
  * @param {string} simulationId
- * @param {string} platform - 'reddit' | 'twitter'
+ * @param {string} platform - default internal mode
  * @param {number} limit - 返回数量
  * @param {number} offset - 偏移量
  */
-export const getSimulationPosts = (simulationId, platform = 'reddit', limit = 50, offset = 0) => {
+export const getSimulationPosts = (simulationId, platform = 'internal', limit = 50, offset = 0) => {
   return service.get(`/api/simulation/${simulationId}/posts`, {
     params: { platform, limit, offset }
   })
diff --git a/backend/app/api/__init__.py b/backend/app/api/__init__.py
index ffda743..0000000 100644
--- a/backend/app/api/__init__.py
+++ b/backend/app/api/__init__.py
@@ -10,6 +10,7 @@ graph_bp = Blueprint('graph', __name__)
 simulation_bp = Blueprint('simulation', __name__)
 report_bp = Blueprint('report', __name__)

+from . import simulation_compat  # noqa: E402, F401
 from . import graph  # noqa: E402, F401
 from . import simulation  # noqa: E402, F401
 from . import report  # noqa: E402, F401
diff --git a/backend/app/api/simulation_compat.py b/backend/app/api/simulation_compat.py
new file mode 100644
index 0000000..0000000
--- /dev/null
+++ b/backend/app/api/simulation_compat.py
@@ -0,0 +1,198 @@
+"""
+Compatibility routes for Render/POC execution.
+
+Imported before the original simulation routes. These routes return a completed
+payload for Step 2 when the generated config exists, and avoid requiring a fixed
+Reddit platform during realtime profile polling.
+"""
+
+import csv
+import json
+import os
+from datetime import datetime
+
+from flask import jsonify, request
+
+from . import simulation_bp
+from ..config import Config
+from ..utils.logger import get_logger
+
+logger = get_logger("mirofish.api.simulation_compat")
+
+
+def _sim_dir(simulation_id):
+    return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
+
+
+def _read_json(path, default=None):
+    if not os.path.exists(path):
+        return default
+    try:
+        with open(path, "r", encoding="utf-8") as f:
+            return json.load(f)
+    except Exception as exc:
+        logger.warning(f"Could not read JSON file {path}: {exc}")
+        return default
+
+
+def _modified_at(path):
+    if not os.path.exists(path):
+        return None
+    return datetime.fromtimestamp(os.stat(path).st_mtime).isoformat()
+
+
+def _read_profiles(sim_dir, platform=None):
+    requested = (platform or "internal").lower()
+
+    reddit_file = os.path.join(sim_dir, "reddit_profiles.json")
+    twitter_file = os.path.join(sim_dir, "twitter_profiles.csv")
+
+    if requested == "twitter" and os.path.exists(twitter_file):
+        try:
+            with open(twitter_file, "r", encoding="utf-8") as f:
+                return list(csv.DictReader(f))
+        except Exception as exc:
+            logger.warning(f"Could not read twitter profile file: {exc}")
+            return []
+
+    data = _read_json(reddit_file, [])
+    return data if isinstance(data, list) else []
+
+
+def _fallback_config(simulation_id, profiles):
+    return {
+        "simulation_id": simulation_id,
+        "generated_at": datetime.now().isoformat(),
+        "llm_model": "fallback-compatible",
+        "mode": "render-compatible-fallback",
+        "enable_reddit": False,
+        "enable_twitter": False,
+        "time_config": {
+            "total_simulation_hours": 5,
+            "minutes_per_round": 60,
+            "agents_per_hour_min": 1,
+            "agents_per_hour_max": max(1, min(len(profiles), 3)),
+            "peak_hours": [9, 10, 14, 15],
+            "peak_activity_multiplier": 1.0,
+            "work_hours": list(range(8, 18)),
+            "work_activity_multiplier": 1.0,
+            "morning_hours": [8, 9, 10, 11],
+            "morning_activity_multiplier": 1.0,
+            "off_peak_hours": [18, 19, 20],
+            "off_peak_activity_multiplier": 0.5,
+        },
+        "agent_configs": [
+            {
+                "agent_id": idx,
+                "entity_name": profile.get("entity_name") or profile.get("username") or profile.get("name") or f"Agent {idx + 1}",
+                "entity_type": profile.get("entity_type") or profile.get("profession") or "internal_agent",
+                "stance": profile.get("stance", "neutral"),
+                "active_hours": list(range(8, 19)),
+                "posts_per_hour": 1,
+                "comments_per_hour": 2,
+                "response_delay_min": 1,
+                "response_delay_max": 5,
+                "activity_level": 0.5,
+                "sentiment_bias": 0,
+                "influence_weight": 1,
+            }
+            for idx, profile in enumerate(profiles)
+        ],
+        "event_config": {
+            "initial_posts": [],
+            "hot_topics": [],
+            "narrative_direction": "Configuração fallback para encerrar o polling do Step 2 no Render.",
+        },
+    }
+
+
+@simulation_bp.route("/<simulation_id>/profiles/realtime", methods=["GET"])
+def get_profiles_realtime_compat(simulation_id):
+    try:
+        sim_dir = _sim_dir(simulation_id)
+        if not os.path.exists(sim_dir):
+            return jsonify({"success": False, "error": f"Simulation not found: {simulation_id}"}), 404
+
+        requested_platform = request.args.get("platform")
+        profiles = _read_profiles(sim_dir, requested_platform)
+        state = _read_json(os.path.join(sim_dir, "state.json"), {}) or {}
+        status = state.get("status", "")
+
+        return jsonify({
+            "success": True,
+            "data": {
+                "simulation_id": simulation_id,
+                "platform": "internal",
+                "platform_requested": requested_platform,
+                "count": len(profiles),
+                "total_expected": state.get("entities_count") or len(profiles),
+                "is_generating": status == "preparing" and not state.get("profiles_generated", False),
+                "file_exists": bool(profiles),
+                "file_modified_at": _modified_at(os.path.join(sim_dir, "reddit_profiles.json")),
+                "profiles": profiles,
+            },
+        })
+    except Exception as exc:
+        logger.error(f"profiles/realtime compatibility error: {exc}")
+        return jsonify({"success": False, "error": str(exc)}), 500
+
+
+@simulation_bp.route("/<simulation_id>/config/realtime", methods=["GET"])
+def get_config_realtime_compat(simulation_id):
+    try:
+        sim_dir = _sim_dir(simulation_id)
+        if not os.path.exists(sim_dir):
+            return jsonify({"success": False, "error": f"Simulation not found: {simulation_id}"}), 404
+
+        config_path = os.path.join(sim_dir, "simulation_config.json")
+        config = _read_json(config_path)
+        profiles = _read_profiles(sim_dir)
+
+        if not config:
+            config = _fallback_config(simulation_id, profiles)
+
+        summary = {
+            "total_agents": len(config.get("agent_configs", [])),
+            "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
+            "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
+            "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
+            "has_twitter_config": bool(config.get("twitter_config")),
+            "has_reddit_config": bool(config.get("reddit_config")),
+            "generated_at": config.get("generated_at"),
+            "llm_model": config.get("llm_model"),
+        }
+
+        return jsonify({
+            "success": True,
+            "data": {
+                "simulation_id": simulation_id,
+                "status": "completed",
+                "phase": "config_ready",
+                "file_exists": os.path.exists(config_path),
+                "file_modified_at": _modified_at(config_path),
+                "is_generating": False,
+                "generation_stage": "completed",
+                "config_generated": True,
+                "config_ready": True,
+                "ready": True,
+                "done": True,
+                "is_ready": True,
+                "summary": summary,
+                "config": config,
+            },
+        })
+    except Exception as exc:
+        logger.error(f"config/realtime compatibility error: {exc}")
+        return jsonify({"success": False, "error": str(exc)}), 500
