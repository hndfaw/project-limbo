import threading
import unittest

from limbo.leases import LeaseError, LeaseStore, run_workers


class LeaseTransitionTests(unittest.TestCase):
    def make_store(self, deps, lease_seconds=30.0):
        self.clock = [0.0]
        return LeaseStore(deps, secret="secret-key", lease_seconds=lease_seconds,
                          clock=lambda: self.clock[0])

    def test_claim_complete_transitions(self):
        store = self.make_store({"a": [], "b": ["a"]})
        self.assertEqual(["a"], store.claimable())
        self.assertEqual("pending", store.status("b"))

        lease = store.claim("w1")
        self.assertEqual("a", lease.task_id)
        self.assertEqual("leased", store.status("a"))
        self.assertEqual([], store.claimable())  # b still needs a

        store.complete(lease.token)
        self.assertEqual("completed", store.status("a"))
        self.assertEqual(["b"], store.claimable())

    def test_dependencies_gate_claiming(self):
        store = self.make_store({"a": [], "b": ["a"], "c": ["b"]})
        # b and c are never claimable until their dependency completes.
        self.assertNotIn("b", store.claimable())
        self.assertNotIn("c", store.claimable())
        store.complete(store.claim("w1", "a").token)
        self.assertEqual(["b"], store.claimable())
        self.assertNotIn("c", store.claimable())

    def test_claim_specific_unavailable_returns_none(self):
        store = self.make_store({"a": [], "b": ["a"]})
        self.assertIsNone(store.claim("w1", "b"))  # dependency not done

    def test_duplicate_claim_returns_none(self):
        store = self.make_store({"a": []})
        first = store.claim("w1", "a")
        self.assertIsNotNone(first)
        self.assertIsNone(store.claim("w2", "a"))

    def test_heartbeat_extends_lease(self):
        store = self.make_store({"a": []}, lease_seconds=10.0)
        lease = store.claim("w1", "a")
        self.assertEqual(10.0, lease.expires_at)
        self.clock[0] = 5.0
        renewed = store.heartbeat(lease.token)
        self.assertEqual(15.0, renewed.expires_at)

    def test_renew_custom_duration(self):
        store = self.make_store({"a": []}, lease_seconds=10.0)
        lease = store.claim("w1", "a")
        renewed = store.renew(lease.token, lease_seconds=100.0)
        self.assertEqual(100.0, renewed.expires_at)


class LeaseExpiryTests(unittest.TestCase):
    def make_store(self, deps, lease_seconds=30.0):
        self.clock = [0.0]
        return LeaseStore(deps, secret="k", lease_seconds=lease_seconds,
                          clock=lambda: self.clock[0])

    def test_expired_lease_becomes_claimable(self):
        store = self.make_store({"a": []}, lease_seconds=30.0)
        store.claim("w1", "a")
        self.assertEqual([], store.claimable())
        self.clock[0] = 31.0
        self.assertEqual(["a"], store.claimable())
        self.assertEqual("ready", store.status("a"))

    def test_expired_worker_is_fenced_out(self):
        store = self.make_store({"a": []}, lease_seconds=30.0)
        stale = store.claim("w1", "a")
        self.clock[0] = 31.0
        fresh = store.claim("w2", "a")  # reclaim
        self.assertEqual("w2", fresh.worker_id)
        # The stale holder can no longer heartbeat or complete.
        with self.assertRaisesRegex(LeaseError, "expired|no longer held"):
            store.heartbeat(stale.token)
        with self.assertRaises(LeaseError):
            store.complete(stale.token)
        # The new holder still owns it.
        store.complete(fresh.token)
        self.assertEqual("completed", store.status("a"))


class LeaseTokenSecurityTests(unittest.TestCase):
    def test_tampered_signature_rejected(self):
        store = LeaseStore({"a": []}, secret="k")
        lease = store.claim("w1", "a")
        tampered = lease.token[:-1] + ("0" if lease.token[-1] != "0" else "1")
        with self.assertRaisesRegex(LeaseError, "signature"):
            store.heartbeat(tampered)

    def test_forged_token_from_other_secret_rejected(self):
        real = LeaseStore({"a": []}, secret="real-secret")
        forger = LeaseStore({"a": []}, secret="guessed-secret")
        forged = forger.claim("attacker", "a").token
        with self.assertRaises(LeaseError):
            real.heartbeat(forged)

    def test_malformed_token_rejected(self):
        store = LeaseStore({"a": []}, secret="k")
        for bad in ("", "not-a-token", "abc.def"):
            with self.assertRaises(LeaseError):
                store.heartbeat(bad)

    def test_empty_secret_rejected(self):
        with self.assertRaises(LeaseError):
            LeaseStore({"a": []}, secret="")


class LeaseFailureTests(unittest.TestCase):
    def test_failure_blocks_dependents(self):
        store = LeaseStore({"a": [], "b": ["a"], "c": ["b"]}, secret="k")
        store.fail(store.claim("w1", "a").token)
        self.assertEqual("failed", store.status("a"))
        self.assertEqual("blocked", store.status("b"))
        self.assertEqual("blocked", store.status("c"))
        self.assertEqual([], store.claimable())
        self.assertTrue(store.finished())

    def test_unknown_dependency_rejected(self):
        with self.assertRaisesRegex(LeaseError, "unknown task"):
            LeaseStore({"a": ["ghost"]}, secret="k")


class WorkerDriverTests(unittest.TestCase):
    def test_single_process_respects_dependency_order(self):
        store = LeaseStore({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}, secret="k")
        order = []
        lock = threading.Lock()

        def execute(task_id):
            with lock:
                order.append(task_id)
            return True

        results = run_workers(store, execute, ["solo"])
        self.assertTrue(all(results.values()))
        self.assertEqual({"a", "b", "c", "d"}, set(results))
        self.assertEqual("a", order[0])
        self.assertEqual("d", order[-1])
        self.assertLess(order.index("b"), order.index("d"))
        self.assertLess(order.index("c"), order.index("d"))

    def test_multiple_workers_each_task_runs_once(self):
        deps = {f"t{i}": [] for i in range(50)}
        store = LeaseStore(deps, secret="k")
        counts = {}
        lock = threading.Lock()

        def execute(task_id):
            with lock:
                counts[task_id] = counts.get(task_id, 0) + 1
            return True

        results = run_workers(store, execute, [f"w{i}" for i in range(8)])
        self.assertEqual(50, len(results))
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertTrue(store.finished())

    def test_concurrent_claims_never_double_lease(self):
        store = LeaseStore({"a": []}, secret="k")
        winners = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def contend(worker_id):
            barrier.wait()
            lease = store.claim(worker_id, "a")
            if lease is not None:
                with lock:
                    winners.append(worker_id)

        threads = [threading.Thread(target=contend, args=(f"w{i}",)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, len(winners))

    def test_failing_task_blocks_dependents_in_driver(self):
        store = LeaseStore({"a": [], "b": ["a"]}, secret="k")

        def execute(task_id):
            return task_id != "a"  # a fails

        results = run_workers(store, execute, ["w1"])
        self.assertEqual({"a": False}, results)  # b never runs (blocked)
        self.assertEqual("blocked", store.status("b"))
        self.assertTrue(store.finished())


class FromPipelineTests(unittest.TestCase):
    def test_builds_from_pipeline_spec(self):
        import json
        import tempfile
        from pathlib import Path

        from limbo.spec import load_pipeline

        with tempfile.TemporaryDirectory() as tmpdir:
            spec = Path(tmpdir) / "limbo.json"
            spec.write_text(json.dumps({"version": 1, "tasks": [
                {"id": "a", "command": "true"},
                {"id": "b", "needs": ["a"], "command": "true"},
            ]}), encoding="utf-8")
            pipeline = load_pipeline(spec)

            store = LeaseStore.from_pipeline(pipeline, secret="k")
            self.assertEqual(["a"], store.claimable())
            store.complete(store.claim("w1", "a").token)
            self.assertEqual(["b"], store.claimable())


if __name__ == "__main__":
    unittest.main()
