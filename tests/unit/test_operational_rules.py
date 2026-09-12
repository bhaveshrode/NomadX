import pytest

# Operational rules scoring logic unit test
def calculate_situation_impact_score(active_anomalies: list, health_report: dict) -> float:
    base_score = len(active_anomalies) * 20.0
    degraded_count = sum(1 for comp_info in health_report["components"].values() if comp_info["status"] in ("DEGRADED", "UNHEALTHY"))
    base_score += degraded_count * 15.0
    
    has_critical = any(a["severity"] in ("CRITICAL", "FINANCIAL_EXCEPTION") for a in active_anomalies)
    if has_critical:
        base_score += 30.0
    if health_report["readiness"]["status"] == "NOT_READY":
        base_score += 30.0
        
    return min(base_score, 100.0)


def build_causality_chain(active_anomalies: list, health_report: dict) -> list:
    causality_chain = []
    components_active = [a["component"] for a in active_anomalies]
    
    if "database" in components_active or health_report["components"]["database"]["status"] == "UNHEALTHY":
        if "api" in components_active:
            causality_chain.append({"upstream": "database", "downstream": "api", "confidence": "HIGH"})
        if any(c in components_active for c in ("ar_event_processor", "settlement_automation_worker", "financial_consistency_automation_worker")):
            causality_chain.append({"upstream": "database", "downstream": "workers", "confidence": "CRITICAL"})
    elif "api" in components_active:
        if any(c in components_active for c in ("ar_event_processor", "settlement_automation_worker", "financial_consistency_automation_worker")):
            causality_chain.append({"upstream": "api", "downstream": "workers", "confidence": "HIGH"})
            
    return causality_chain


def test_situation_scoring_clean_state():
    anomalies = []
    health = {
        "readiness": {"status": "READY"},
        "components": {
            "database": {"status": "HEALTHY"},
            "api": {"status": "HEALTHY"}
        }
    }
    assert calculate_situation_impact_score(anomalies, health) == 0.0


def test_situation_scoring_multiple_failures():
    # 2 anomalies, 1 degraded component, readiness status NOT_READY, has critical
    anomalies = [
        {"component": "api", "severity": "CRITICAL"},
        {"component": "database", "severity": "MEDIUM"}
    ]
    health = {
        "readiness": {"status": "NOT_READY"},
        "components": {
            "database": {"status": "DEGRADED"},
            "api": {"status": "HEALTHY"}
        }
    }
    # base_score = 2 * 20.0 (40.0) + 1 * 15.0 (15.0) + 30.0 (critical) + 30.0 (not ready) = 115.0 -> capped at 100.0
    assert calculate_situation_impact_score(anomalies, health) == 100.0


def test_causality_chain_database_propagation():
    anomalies = [
        {"component": "api"},
        {"component": "ar_event_processor"}
    ]
    health = {
        "components": {
            "database": {"status": "UNHEALTHY"}
        }
    }
    chain = build_causality_chain(anomalies, health)
    assert len(chain) == 2
    assert chain[0]["upstream"] == "database"
    assert chain[0]["downstream"] == "api"
    assert chain[1]["downstream"] == "workers"


def test_causality_chain_api_propagation():
    anomalies = [
        {"component": "api"},
        {"component": "ar_event_processor"}
    ]
    health = {
        "components": {
            "database": {"status": "HEALTHY"}
        }
    }
    chain = build_causality_chain(anomalies, health)
    assert len(chain) == 1
    assert chain[0]["upstream"] == "api"
    assert chain[0]["downstream"] == "workers"
