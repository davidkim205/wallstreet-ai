
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class AnalysisResult:
    query: str
    ticker: str
    analysis_type: str
    data_context: dict
    llm_response: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

@dataclass
class MarketData:
    ticker:             str
    price_data:         dict
    fundamentals:       dict
    technicals:         dict
    news_snippets:      list
    earnings_data:      dict  = field(default_factory=dict)
    web_search_results: list  = field(default_factory=list)
