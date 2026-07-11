import unittest

from limbo.errors import SpecError
from limbo.retry import RetryPolicy, parse_retry_policy


class RetryPolicyParsingTests(unittest.TestCase):
    def test_default_is_single_attempt(self):
        policy = parse_retry_policy(None, "t")
        self.assertEqual(1, policy.max_attempts)
        self.assertEqual("fixed", policy.backoff)
        self.assertEqual(0.0, policy.delay_seconds)
        self.assertEqual((), policy.retry_on_exit_codes)
        self.assertTrue(policy.retry_on_timeout)

    def test_parses_full_policy(self):
        policy = parse_retry_policy(
            {"max_attempts": 4, "backoff": "exponential", "delay_seconds": 0.5,
             "max_delay_seconds": 5, "retry_on_exit_codes": [2, 3], "retry_on_timeout": False},
            "t",
        )
        self.assertEqual(4, policy.max_attempts)
        self.assertEqual("exponential", policy.backoff)
        self.assertEqual(0.5, policy.delay_seconds)
        self.assertEqual(5.0, policy.max_delay_seconds)
        self.assertEqual((2, 3), policy.retry_on_exit_codes)
        self.assertFalse(policy.retry_on_timeout)

    def test_rejects_non_object(self):
        with self.assertRaisesRegex(SpecError, "retry must be an object"):
            parse_retry_policy([1, 2], "t")

    def test_rejects_unknown_field(self):
        with self.assertRaisesRegex(SpecError, "unknown retry field"):
            parse_retry_policy({"attempts": 3}, "t")

    def test_rejects_bad_max_attempts(self):
        for bad in (0, -1, 1.5, True, "2"):
            with self.assertRaises(SpecError):
                parse_retry_policy({"max_attempts": bad}, "t")

    def test_rejects_bad_backoff(self):
        with self.assertRaisesRegex(SpecError, "backoff must be one of"):
            parse_retry_policy({"backoff": "quadratic"}, "t")

    def test_rejects_negative_delay(self):
        with self.assertRaises(SpecError):
            parse_retry_policy({"delay_seconds": -1}, "t")

    def test_rejects_non_integer_exit_codes(self):
        with self.assertRaises(SpecError):
            parse_retry_policy({"retry_on_exit_codes": [1, "2"]}, "t")
        with self.assertRaises(SpecError):
            parse_retry_policy({"retry_on_exit_codes": [True]}, "t")


class BackoffTests(unittest.TestCase):
    def test_fixed_backoff(self):
        policy = RetryPolicy(delay_seconds=2.0, backoff="fixed")
        self.assertEqual(2.0, policy.delay_for(1))
        self.assertEqual(2.0, policy.delay_for(5))

    def test_linear_backoff(self):
        policy = RetryPolicy(delay_seconds=1.5, backoff="linear")
        self.assertEqual(1.5, policy.delay_for(1))
        self.assertEqual(4.5, policy.delay_for(3))

    def test_exponential_backoff(self):
        policy = RetryPolicy(delay_seconds=1.0, backoff="exponential")
        self.assertEqual(1.0, policy.delay_for(1))
        self.assertEqual(2.0, policy.delay_for(2))
        self.assertEqual(8.0, policy.delay_for(4))

    def test_max_delay_caps_backoff(self):
        policy = RetryPolicy(delay_seconds=1.0, backoff="exponential", max_delay_seconds=3.0)
        self.assertEqual(3.0, policy.delay_for(5))

    def test_zero_delay_returns_zero(self):
        self.assertEqual(0.0, RetryPolicy(delay_seconds=0.0, backoff="exponential").delay_for(3))


class RetryableTests(unittest.TestCase):
    def test_timeout_respects_flag(self):
        self.assertTrue(RetryPolicy(retry_on_timeout=True).is_retryable(None, timed_out=True))
        self.assertFalse(RetryPolicy(retry_on_timeout=False).is_retryable(None, timed_out=True))

    def test_zero_exit_code_never_retryable(self):
        self.assertFalse(RetryPolicy().is_retryable(0, timed_out=False))

    def test_any_nonzero_retryable_by_default(self):
        self.assertTrue(RetryPolicy().is_retryable(1, timed_out=False))
        self.assertTrue(RetryPolicy().is_retryable(137, timed_out=False))

    def test_explicit_exit_codes_restrict_retries(self):
        policy = RetryPolicy(retry_on_exit_codes=(2, 3))
        self.assertTrue(policy.is_retryable(2, timed_out=False))
        self.assertFalse(policy.is_retryable(1, timed_out=False))


if __name__ == "__main__":
    unittest.main()
