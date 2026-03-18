VALID_DECISIONS = [
    "approve_restart",
    "deny_restart",
    "request_more_info"
]

def approval_node(state):
    print("?? approval_node running")
    return {"approved": True}
