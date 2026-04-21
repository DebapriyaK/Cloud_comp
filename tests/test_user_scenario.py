"""
TEST CASE 1: The exact scenario from the methodology discussion.

Expected findings: 2
  - Line 10: G5 . Membership  |  POSSIBLE  (allowed_members is a parameter)
  - Line 17: G5 . Membership  |  CONFIRMED (REGISTERED is a known list literal)

This tests concern A directly:
  - Function parameters -> cannot confirm type -> POSSIBLE
  - Module-level list literal -> confirmed list -> CONFIRMED
"""

def check_permission(user_id, allowed_members):
    if user_id in allowed_members:
        return True
    return False


REGISTERED = [101, 102, 103, 201, 202, 203, 301, 302, 303, 401]

def is_registered(user_id):
    if user_id in REGISTERED:
        return True
    return False
