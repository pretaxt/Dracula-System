"""资金费率套利策略包

Week 3 实现: FundingRateScanner — 扫描并筛选资金费率机会
Week 5 实现: FundingRateStrategy — 完整策略(开/平仓、持仓监控)
"""
from app.strategies.funding_rate.scanner import FundingRateOpportunity, FundingRateScanner

__all__ = ["FundingRateScanner", "FundingRateOpportunity"]
