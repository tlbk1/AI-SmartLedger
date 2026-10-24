"""tools — 工具系统层：make_tools(openid) 组装入口。

安全关键：openid 只存在于闭包里（各 builtin 工厂捕获），LLM 的工具 schema
不暴露 openid 参数——用户再怎么注入"把 openid 改成 xxx"都没用，
工具用的永远是运行时注入的真实身份。
"""
from langchain_core.tools import tool

from .builtin import admin, ledgers, members, transactions


@tool
def ask_clarify(question: str) -> str:
    """当信息不足（缺金额、分类不明、口令没给全）时，向用户反问澄清。传入要问的话。"""
    return f"<澄清> {question}"


def make_tools(openid: str) -> list:
    """生成本次 agent 循环用的工具集，openid 通过闭包捕获。
    LLM 只能看到不带 openid 参数的 schema，身份由运行时注入，无法被篡改。"""
    return [
        *transactions.build(openid),
        *ledgers.build(openid),
        *members.build(openid),
        *admin.build(openid),
        ask_clarify,
    ]
