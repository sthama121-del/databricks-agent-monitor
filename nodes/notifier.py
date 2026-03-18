def notifier_node(state):
    print("?? notifier_node running")
    state["notified"] = True
    return state
