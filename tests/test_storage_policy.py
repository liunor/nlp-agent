from server.storage.policy import StorageBucket, policy_for_roles, usage_ratio, usage_state


def test_roles_use_highest_policy_without_adding_quotas() -> None:
    student = policy_for_roles({"student"})
    teacher = policy_for_roles({"student", "teacher"})

    assert teacher.core_quota_bytes == 256 * 1024 * 1024
    assert teacher.files_quota_bytes == 512 * 1024 * 1024
    assert teacher.total_quota_bytes == 768 * 1024 * 1024
    assert teacher.total_quota_bytes != student.total_quota_bytes + teacher.total_quota_bytes


def test_unknown_roles_are_safe_and_guests_are_small() -> None:
    guest = policy_for_roles({"unknown"})

    assert guest.core_quota_bytes == 32 * 1024 * 1024
    assert guest.files_quota_bytes == 16 * 1024 * 1024
    assert guest.max_file_bytes == 5 * 1024 * 1024


def test_usage_state_matches_progress_bar_thresholds() -> None:
    quota = 100

    assert usage_ratio(25, quota) == 0.25
    assert usage_state(79, quota) == "normal"
    assert usage_state(80, quota) == "warning"
    assert usage_state(90, quota) == "critical"
    assert usage_state(100, quota) == "full"
    assert usage_state(150, quota) == "full"


def test_bucket_quota_is_explicit() -> None:
    policy = policy_for_roles({"teacher"})

    assert policy.quota_for(StorageBucket.CORE) == policy.core_quota_bytes
    assert policy.quota_for(StorageBucket.FILES) == policy.files_quota_bytes
