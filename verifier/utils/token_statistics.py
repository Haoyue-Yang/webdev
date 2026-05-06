"""
统计token
"""
import traceback
from flask import g


def save_usage(usage):
    """
    save_usage
    :param usage:
    :return:
    """
    try:
        if not hasattr(g, 'tokens_usage'):
            g.tokens_usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }

        for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
            g.tokens_usage[key] += usage.get(key, 0)
    except Exception as ex:
        print(traceback.format_exc())
        pass
    return


def get_usage():
    """
    get_usage
    :return:
    """
    try:
        if not hasattr(g, 'tokens_usage'):
            g.tokens_usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }

        return g.tokens_usage
    except Exception as ex:
        print(traceback.format_exc())
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }



