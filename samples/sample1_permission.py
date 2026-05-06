ALLOWED_USERS = [1001, 1002, 1003, 1004, 1005,
                 1006, 1007, 1008, 1009, 1010]

def check_permission(user_id):
    if user_id in ALLOWED_USERS:
        return True
    return False
