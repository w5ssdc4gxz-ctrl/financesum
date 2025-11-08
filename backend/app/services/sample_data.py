"""Static sample data used when external data providers are unavailable."""

from __future__ import annotations

from typing import Any, Dict, List


SampleStatements = Dict[str, Any]


sample_filings_by_ticker: Dict[str, List[Dict[str, Any]]] = {
    "GOOGL": [
        {
            "filing_type": "10-K",
            "filing_date": "2023-12-31",
            "url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204424000019/goog-20231231x10k.htm",
            "income_statement": {
                "totalRevenue": 307394000000,
                "costOfRevenue": 135918000000,
                "grossProfit": 171476000000,
                "operatingIncome": 84342000000,
                "netIncome": 73228000000,
                "ebitda": 100215000000,
                "interestExpense": 134000000,
                "totalOperatingExpenses": 123052000000,
            },
            "balance_sheet": {
                "totalAssets": 402097000000,
                "totalCurrentAssets": 195656000000,
                "cash": 38596000000,
                "netReceivables": 47052000000,
                "inventory": 2072000000,
                "totalLiab": 121560000000,
                "totalCurrentLiabilities": 81720000000,
                "shortTermDebt": 5450000000,
                "longTermDebt": 14064000000,
                "totalStockholderEquity": 280537000000,
                "retainedEarnings": 262742000000,
            },
            "cash_flow": {
                "totalCashFromOperatingActivities": 101700000000,
                "capitalExpenditures": -36454000000,
                "totalCashflowsFromInvestingActivities": -37648000000,
                "totalCashFromFinancingActivities": -51798000000,
            },
        },
        {
            "filing_type": "10-Q",
            "filing_date": "2024-06-30",
            "url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204424000043/goog-20240630x10q.htm",
            "income_statement": {
                "totalRevenue": 80644000000,
                "costOfRevenue": 35071000000,
                "grossProfit": 45573000000,
                "operatingIncome": 25025000000,
                "netIncome": 19704000000,
                "ebitda": 26893000000,
                "interestExpense": 36000000,
                "totalOperatingExpenses": 55619000000,
            },
            "balance_sheet": {
                "totalAssets": 414980000000,
                "totalCurrentAssets": 206870000000,
                "cash": 39432000000,
                "netReceivables": 49587000000,
                "inventory": 2178000000,
                "totalLiab": 125460000000,
                "totalCurrentLiabilities": 82930000000,
                "shortTermDebt": 5500000000,
                "longTermDebt": 14060000000,
                "totalStockholderEquity": 289520000000,
                "retainedEarnings": 271130000000,
            },
            "cash_flow": {
                "totalCashFromOperatingActivities": 50640000000,
                "capitalExpenditures": -18320000000,
                "totalCashflowsFromInvestingActivities": -19150000000,
                "totalCashFromFinancingActivities": -24610000000,
            },
        },
        {
            "filing_type": "10-Q",
            "filing_date": "2024-03-31",
            "url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204424000029/goog-20240331x10q.htm",
            "income_statement": {
                "totalRevenue": 80740000000,
                "costOfRevenue": 34798000000,
                "grossProfit": 45942000000,
                "operatingIncome": 25342000000,
                "netIncome": 21360000000,
                "ebitda": 27284000000,
                "interestExpense": 35000000,
                "totalOperatingExpenses": 55398000000,
            },
            "balance_sheet": {
                "totalAssets": 408270000000,
                "totalCurrentAssets": 203110000000,
                "cash": 40361000000,
                "netReceivables": 49885000000,
                "inventory": 2145000000,
                "totalLiab": 122940000000,
                "totalCurrentLiabilities": 82040000000,
                "shortTermDebt": 5480000000,
                "longTermDebt": 14061000000,
                "totalStockholderEquity": 285330000000,
                "retainedEarnings": 266940000000,
            },
            "cash_flow": {
                "totalCashFromOperatingActivities": 52020000000,
                "capitalExpenditures": -17560000000,
                "totalCashflowsFromInvestingActivities": -18470000000,
                "totalCashFromFinancingActivities": -25730000000,
            },
        },
        {
            "filing_type": "10-Q",
            "filing_date": "2023-09-30",
            "url": "https://www.sec.gov/Archives/edgar/data/1652044/000165204423000123/goog-20230930x10q.htm",
            "income_statement": {
                "totalRevenue": 76299000000,
                "costOfRevenue": 34052000000,
                "grossProfit": 42247000000,
                "operatingIncome": 21746000000,
                "netIncome": 19266000000,
                "ebitda": 25139000000,
                "interestExpense": 34000000,
                "totalOperatingExpenses": 54553000000,
            },
            "balance_sheet": {
                "totalAssets": 395840000000,
                "totalCurrentAssets": 190870000000,
                "cash": 38722000000,
                "netReceivables": 46234000000,
                "inventory": 2048000000,
                "totalLiab": 119640000000,
                "totalCurrentLiabilities": 80480000000,
                "shortTermDebt": 5300000000,
                "longTermDebt": 14059000000,
                "totalStockholderEquity": 276200000000,
                "retainedEarnings": 258830000000,
            },
            "cash_flow": {
                "totalCashFromOperatingActivities": 48990000000,
                "capitalExpenditures": -16920000000,
                "totalCashflowsFromInvestingActivities": -17760000000,
                "totalCashFromFinancingActivities": -23840000000,
            },
        },
    ]
}


