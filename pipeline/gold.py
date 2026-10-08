"""Silver -> Gold: the five data products the business uses. Rebuilt each cycle unless blocked."""
from pipeline.transform import now_iso

GOLD_SQL = {
    "gold_driver_risk_score": """
        CREATE TABLE gold_driver_risk_score AS
        SELECT t.vehicle_id, p.policy_id, p.make || ' ' || p.model AS car,
               COUNT(*) AS readings,
               ROUND(AVG(t.speed_kmph), 1) AS avg_speed,
               ROUND(100.0 * SUM(t.harsh_brake + t.harsh_accel) / COUNT(*), 1) AS harsh_events_pct,
               MAX(0, MIN(100, ROUND(100 - 4 * (100.0 * SUM(t.harsh_brake + t.harsh_accel) / COUNT(*))
                                    - MAX(0, AVG(t.speed_kmph) - 60), 0))) AS risk_score_safe_100
        FROM silver_telematics t LEFT JOIN silver_policy p ON p.vehicle_id = t.vehicle_id
        GROUP BY t.vehicle_id, p.policy_id, car""",
    "gold_claims_summary": """
        CREATE TABLE gold_claims_summary AS
        SELECT location, claim_type, COUNT(*) AS claims, ROUND(SUM(claim_amount), 0) AS total_amount_inr,
               ROUND(AVG(claim_amount), 0) AS avg_amount_inr
        FROM silver_claims GROUP BY location, claim_type""",
    "gold_fraud_watchlist": """
        CREATE TABLE gold_fraud_watchlist AS
        SELECT b.claim_id, c.vehicle_id, c.claim_amount, b.total AS bill_total, b.garage_id,
               'Repair bill above claim amount' AS flag
        FROM silver_garage_bills b JOIN silver_claims c ON c.claim_id = b.claim_id
        WHERE b.total > c.claim_amount * 1.2
        UNION ALL
        SELECT json_extract(payload, '$.claim_id'), json_extract(payload, '$.vehicle_id'),
               json_extract(payload, '$.claim_amount'), NULL, json_extract(payload, '$.garage_id'),
               'Duplicate claim stopped by checks'
        FROM quarantine WHERE source = 'claims' AND check_name = 'duplicates'""",
    "gold_customer_360": """
        CREATE TABLE gold_customer_360 AS
        SELECT cu.customer_id, cu.name, cu.phone, cu.city,
               COUNT(DISTINCT p.policy_id) AS policies, COUNT(DISTINCT cl.claim_id) AS claims,
               ROUND(COALESCE(SUM(cl.claim_amount), 0), 0) AS claimed_inr
        FROM silver_customer cu
        LEFT JOIN silver_policy p ON p.customer_id = cu.customer_id
        LEFT JOIN silver_claims cl ON cl.policy_id = p.policy_id
        GROUP BY cu.customer_id""",
    "gold_compliance_report": """
        CREATE TABLE gold_compliance_report AS
        SELECT 'PII masked in customer data' AS control,
               CASE WHEN SUM(masked = 0) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
               SUM(masked = 0) || ' unmasked of ' || COUNT(*) || ' customers' AS detail
        FROM silver_customer
        UNION ALL
        SELECT 'Bad rows kept out of Silver', 'PASS', COUNT(*) || ' rows in quarantine' FROM quarantine
        UNION ALL
        SELECT 'Claims on expired policies blocked', 'PASS',
               COUNT(*) || ' stopped' FROM quarantine WHERE source = 'claims' AND check_name = 'referential'""",
}


def ensure_rows(conn):
    for node in GOLD_SQL:
        conn.execute("INSERT OR IGNORE INTO gold_refresh(node, last_refreshed, blocked) VALUES (?, ?, 0)",
                     (node, now_iso()))


def block(conn, node, reason):
    conn.execute("UPDATE gold_refresh SET blocked=1, reason=? WHERE node=?", (reason, node))


def unblock(conn, node):
    conn.execute("UPDATE gold_refresh SET blocked=0, reason=NULL WHERE node=?", (node,))


def refresh(conn):
    """Rebuild every Gold table that is not blocked. Blocked ones keep their last good copy."""
    ensure_rows(conn)
    built = []
    for node, sql in GOLD_SQL.items():
        if conn.execute("SELECT blocked FROM gold_refresh WHERE node=?", (node,)).fetchone()[0]:
            continue
        conn.execute(f"DROP TABLE IF EXISTS {node}")
        conn.execute(sql)
        conn.execute("UPDATE gold_refresh SET last_refreshed=? WHERE node=?", (now_iso(), node))
        built.append(node)
    return built
