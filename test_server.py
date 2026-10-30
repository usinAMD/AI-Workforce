import unittest
import server
class CoreTests(unittest.TestCase):
 def test_classification(self):
  self.assertEqual(server.category("Build a website"),"Development")
  self.assertEqual(server.category("Analyze CSV data"),"Analysis")
 def test_plan(self):
  self.assertEqual([x["role"] for x in server.plan("Write a report")],["Planner","Researcher","Reviewer"])
  self.assertEqual([x["role"] for x in server.plan("Build a website")],["Planner","Developer","Reviewer"])
 def test_token_estimate(self):
  self.assertEqual(server.estimate_tokens("12345678"),2)
  self.assertEqual(server.estimate_tokens(""),0)
 def test_free_and_paid_registry(self):
  self.assertFalse(server.P["ollama"][3]); self.assertTrue(server.P["openai"][3])
if __name__=="__main__": unittest.main()
