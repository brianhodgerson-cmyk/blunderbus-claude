"""Push updated Household Finance dashboard to Grafana."""
import os, sys, json, urllib.request, urllib.error

GRAFANA_URL = "http://192.168.50.202:3000"
DS_UID = "afh4serms9wcge"
DS = {"type": "grafana-clickhouse-datasource", "uid": DS_UID}

# Excluded categories — noise
EXCL = "category NOT IN ('Transfer', 'Credit Card Payment')"
INCOME_CAT = "category IN ('Paychecks', 'Other Income')"
FIXED_CAT = "category IN ('Mortgage', 'Gas & Electric', 'Insurance', 'Phone', 'Internet & Cable', 'Garbage')"
ESSENTIAL_CAT = "category IN ('Groceries', 'Gas', 'Medical')"

dashboard = {
    "id": 4,
    "uid": "5bdb7e07-a42a-46ef-8690-bd6b9cd1815a",
    "title": "Household Finance",
    "tags": ["finance", "monarch"],
    "timezone": "browser",
    "refresh": "1d",
    "time": {"from": "now-90d", "to": "now"},
    "schemaVersion": 39,
    "version": 2,
    "panels": [

        # ── Row 1: Key stats ──────────────────────────────────────────────
        {"id":1,"gridPos":{"h":4,"w":6,"x":0,"y":0},"type":"stat","title":"Net Worth",
         "datasource":DS,
         "options":{"colorMode":"background","graphMode":"none"},
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0,
             "color":{"mode":"fixed","fixedColor":"blue"},
             "thresholds":{"steps":[{"color":"blue","value":None}]}}},
         "targets":[{"rawSql":"SELECT sum(balance) as \"Net Worth\" FROM finance.accounts FINAL WHERE snapshot_date = today()","format":1,"refId":"A"}]},

        {"id":2,"gridPos":{"h":4,"w":6,"x":6,"y":0},"type":"stat","title":"Investments",
         "datasource":DS,
         "options":{"colorMode":"background","graphMode":"none"},
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0,
             "color":{"mode":"fixed","fixedColor":"green"},
             "thresholds":{"steps":[{"color":"green","value":None}]}}},
         "targets":[{"rawSql":"SELECT sum(balance) as \"Investments\" FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type = 'brokerage'","format":1,"refId":"A"}]},

        {"id":3,"gridPos":{"h":4,"w":6,"x":12,"y":0},"type":"stat","title":"Cash & Savings",
         "datasource":DS,
         "options":{"colorMode":"background","graphMode":"none"},
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0,
             "color":{"mode":"fixed","fixedColor":"teal"},
             "thresholds":{"steps":[{"color":"teal","value":None}]}}},
         "targets":[{"rawSql":"SELECT sum(balance) as \"Cash\" FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type = 'depository'","format":1,"refId":"A"}]},

        {"id":4,"gridPos":{"h":4,"w":6,"x":18,"y":0},"type":"stat","title":"True Spend (Selected Period)",
         "datasource":DS,
         "options":{"colorMode":"background","graphMode":"none"},
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0,
             "thresholds":{"mode":"absolute","steps":[
                 {"color":"green","value":None},
                 {"color":"yellow","value":8000},
                 {"color":"red","value":12000}]}}},
         "targets":[{"rawSql":f"SELECT abs(sum(amount)) as \"Spend\" FROM finance.transactions WHERE $__timeFilter(date) AND amount < 0 AND {EXCL}","format":1,"refId":"A"}]},

        # ── Row 2: Monthly cash flow + savings rate ───────────────────────
        {"id":5,"gridPos":{"h":9,"w":16,"x":0,"y":4},"type":"barchart",
         "title":"Monthly Income vs True Spending",
         "description":"Transfers and credit card payments excluded. Use time picker to change range.",
         "datasource":DS,
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0}},
         "options":{"orientation":"auto","groupWidth":0.7,"fillOpacity":70,"legend":{"displayMode":"list","placement":"bottom"}},
         "targets":[
             {"rawSql":f"SELECT toStartOfMonth(date) as month, sum(if({INCOME_CAT}, amount, 0)) as \"Income\" FROM finance.transactions WHERE $__timeFilter(date) GROUP BY month ORDER BY month","format":1,"refId":"Income"},
             {"rawSql":f"SELECT toStartOfMonth(date) as month, abs(sum(if(amount < 0 AND {EXCL}, amount, 0))) as \"Spending\" FROM finance.transactions WHERE $__timeFilter(date) GROUP BY month ORDER BY month","format":1,"refId":"Spending"},
         ]},

        {"id":6,"gridPos":{"h":4,"w":8,"x":16,"y":4},"type":"gauge",
         "title":"Savings Rate (Selected Period)",
         "datasource":DS,
         "options":{"minValue":0,"maxValue":100,"showThresholdLabels":True,"showThresholdMarkers":True},
         "fieldConfig":{"defaults":{"unit":"percent","decimals":1,"min":0,"max":100,
             "thresholds":{"steps":[
                 {"color":"red","value":0},
                 {"color":"yellow","value":15},
                 {"color":"green","value":25}]}}},
         "targets":[{"rawSql":f"SELECT round(100*(income-spending)/income,1) as \"Savings Rate\" FROM (SELECT sum(if({INCOME_CAT},amount,0)) as income, abs(sum(if(amount<0 AND {EXCL},amount,0))) as spending FROM finance.transactions WHERE $__timeFilter(date))","format":1,"refId":"A"}]},

        {"id":7,"gridPos":{"h":5,"w":8,"x":16,"y":8},"type":"stat",
         "title":"Avg Monthly Surplus (Selected Period)",
         "datasource":DS,
         "options":{"colorMode":"background","graphMode":"area","textMode":"value_and_name"},
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0,
             "color":{"mode":"thresholds"},
             "thresholds":{"steps":[{"color":"red","value":None},{"color":"yellow","value":2000},{"color":"green","value":5000}]}}},
         "targets":[{"rawSql":f"SELECT round((sum(if({INCOME_CAT},amount,0)) - abs(sum(if(amount<0 AND {EXCL},amount,0)))) / greatest(dateDiff('month', toDate($__from/1000), toDate($__to/1000)), 1), 0) as \"Avg Monthly Surplus\" FROM finance.transactions WHERE $__timeFilter(date)","format":1,"refId":"A"}]},

        # ── Row 3: Category breakdown + Fixed vs Discretionary ───────────
        {"id":8,"gridPos":{"h":9,"w":12,"x":0,"y":13},"type":"barchart",
         "title":"Top Spending Categories (Selected Period)",
         "datasource":DS,
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0}},
         "options":{"orientation":"horizontal","fillOpacity":70},
         "targets":[{"rawSql":f"SELECT category, abs(sum(amount)) as total FROM finance.transactions WHERE $__timeFilter(date) AND amount < 0 AND {EXCL} GROUP BY category ORDER BY total DESC LIMIT 12","format":1,"refId":"A"}]},

        {"id":9,"gridPos":{"h":9,"w":12,"x":12,"y":13},"type":"barchart",
         "title":"Fixed vs Discretionary (Selected Period)",
         "description":"Fixed = mortgage, utilities, insurance, phone. Essential = groceries, gas, medical. Discretionary = everything else.",
         "datasource":DS,
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0}},
         "options":{"orientation":"horizontal","fillOpacity":70,"legend":{"displayMode":"list","placement":"bottom"}},
         "targets":[{"rawSql":f"SELECT multiIf({FIXED_CAT},'Fixed',{ESSENTIAL_CAT},'Essential','Discretionary') as type, abs(sum(amount)) as total FROM finance.transactions WHERE $__timeFilter(date) AND amount < 0 AND {EXCL} GROUP BY type ORDER BY total DESC","format":1,"refId":"A"}]},

        # ── Row 4: Net worth breakdown ────────────────────────────────────
        {"id":10,"gridPos":{"h":8,"w":12,"x":0,"y":22},"type":"barchart",
         "title":"Balance by Account Type (Today)",
         "description":"Grouped by type so mortgage does not net against savings.",
         "datasource":DS,
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0}},
         "options":{"orientation":"horizontal","fillOpacity":70},
         "targets":[{"rawSql":"SELECT multiIf(account_type='brokerage','Investments',account_type='depository','Cash & Savings',account_type='loan','Mortgage',account_type='credit','Credit Cards',account_type) as type, sum(balance) as total FROM finance.accounts FINAL WHERE snapshot_date = today() GROUP BY type ORDER BY total DESC","format":1,"refId":"A"}]},

        {"id":12,"gridPos":{"h":8,"w":12,"x":12,"y":22},"type":"barchart",
         "title":"Net Worth by Type",
         "datasource":DS,
         "fieldConfig":{"defaults":{"unit":"currencyUSD","decimals":0}},
         "options":{"orientation":"horizontal","fillOpacity":70},
         "targets":[
             {"rawSql":"SELECT 'Investments' as type, sum(balance) as total FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type='brokerage'","format":1,"refId":"Inv"},
             {"rawSql":"SELECT 'Cash & Savings' as type, sum(balance) as total FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type='depository'","format":1,"refId":"Cash"},
             {"rawSql":"SELECT 'Mortgage' as type, sum(balance) as total FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type='loan'","format":1,"refId":"Loan"},
             {"rawSql":"SELECT 'Credit Cards' as type, sum(balance) as total FROM finance.accounts FINAL WHERE snapshot_date = today() AND account_type='credit'","format":1,"refId":"CC"},
         ]},

        # ── Row 5: Transactions ───────────────────────────────────────────
        {"id":11,"gridPos":{"h":10,"w":24,"x":0,"y":30},"type":"table",
         "title":"Recent Transactions (Transfers Hidden)",
         "datasource":DS,
         "fieldConfig":{"defaults":{"custom":{"align":"auto"}},
             "overrides":[
                 {"matcher":{"id":"byName","options":"amount"},"properties":[{"id":"unit","value":"currencyUSD"},{"id":"custom.width","value":120}]},
                 {"matcher":{"id":"byName","options":"date"},"properties":[{"id":"custom.width","value":110}]},
                 {"matcher":{"id":"byName","options":"category"},"properties":[{"id":"custom.width","value":160}]},
             ]},
         "options":{"sortBy":[{"desc":True,"displayName":"date"}],
             "footer":{"show":True,"reducer":["sum"],"fields":["amount"]}},
         "targets":[{"rawSql":"SELECT date, merchant, category, account_name, abs(amount) as amount FROM finance.transactions WHERE $__timeFilter(date) AND category NOT IN ('Transfer') ORDER BY date DESC LIMIT 75","format":1,"refId":"A"}]},
    ],
}

payload = json.dumps({"dashboard": dashboard, "folderId": 0, "overwrite": True}).encode()

api_key = os.environ["GRAFANA_KEY"]
req = urllib.request.Request(
    f"{GRAFANA_URL}/api/dashboards/db",
    data=payload,
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        print(f"Status: {result.get('status')}")
        print(f"URL: {result.get('url')}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode()}")
    sys.exit(1)
