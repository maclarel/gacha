#!/usr/bin/env python3
"""
Basic tests for Gacha file server.
These tests validate core functionality without requiring a running server.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
import yaml

# Add parent directory to path to import gacha
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gacha import load_config, load_rules, RuleValidator


class TestConfigLoading(unittest.TestCase):
    """Test configuration file loading."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_valid_config(self):
        """Test loading a valid configuration file."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'hostname': 'localhost',
                'listen_port': 8080
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)

        config = load_config(config_path)
        self.assertEqual(config['hostname'], 'localhost')
        self.assertEqual(config['listen_port'], 8080)

    def test_load_config_missing_hostname(self):
        """Test that missing hostname raises error."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'listen_port': 8080
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)

        with self.assertRaises(ValueError):
            load_config(config_path)

    def test_load_config_missing_port(self):
        """Test that missing port raises error."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'hostname': 'localhost'
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)

        with self.assertRaises(ValueError):
            load_config(config_path)

    def test_load_config_file_not_found(self):
        """Test that missing config file raises error."""
        config_path = os.path.join(self.test_dir, 'nonexistent.yaml')
        
        with self.assertRaises(FileNotFoundError):
            load_config(config_path)


class TestRuleLoading(unittest.TestCase):
    """Test rule file loading."""

    def setUp(self):
        """Create temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.rules_dir = os.path.join(self.test_dir, 'rules')
        os.makedirs(self.rules_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_valid_rule(self):
        """Test loading a valid rule file."""
        rule_path = os.path.join(self.rules_dir, 'test.yaml')
        rule_data = {
            'rule': {
                'path': 'files/test.txt',
                'request_uri': '/test'
            }
        }
        with open(rule_path, 'w') as f:
            yaml.dump(rule_data, f)

        rules = load_rules(self.rules_dir)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].path, 'files/test.txt')
        self.assertEqual(rules[0].request_uri, '/test')

    def test_load_rule_missing_path(self):
        """Test that rule without path is skipped."""
        rule_path = os.path.join(self.rules_dir, 'invalid.yaml')
        rule_data = {
            'rule': {
                'request_uri': '/test'
            }
        }
        with open(rule_path, 'w') as f:
            yaml.dump(rule_data, f)

        rules = load_rules(self.rules_dir)
        self.assertEqual(len(rules), 0)

    def test_load_rule_missing_request_uri(self):
        """Test that rule without request_uri is skipped."""
        rule_path = os.path.join(self.rules_dir, 'invalid.yaml')
        rule_data = {
            'rule': {
                'path': 'files/test.txt'
            }
        }
        with open(rule_path, 'w') as f:
            yaml.dump(rule_data, f)

        rules = load_rules(self.rules_dir)
        self.assertEqual(len(rules), 0)

    def test_load_empty_rules_dir(self):
        """Test loading from empty rules directory."""
        rules = load_rules(self.rules_dir)
        self.assertEqual(len(rules), 0)


class TestRuleValidation(unittest.TestCase):
    """Test rule validation logic."""

    def test_simple_rule_match(self):
        """Test simple rule matching with just URI."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Should match
        self.assertTrue(rule.validate('/test', {}))
        
        # Should not match
        self.assertFalse(rule.validate('/other', {}))

    def test_header_validation(self):
        """Test header validation."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'header': 'X-API-Key',
            'header_value': 'secret123'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Should match with correct header
        headers = {'X-API-Key': 'secret123'}
        self.assertTrue(rule.validate('/test', headers))
        
        # Should not match with wrong header value
        headers = {'X-API-Key': 'wrong'}
        self.assertFalse(rule.validate('/test', headers))
        
        # Should not match without header
        self.assertFalse(rule.validate('/test', {}))

    def test_header_multiple_values(self):
        """Test header validation with multiple acceptable values."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'header': 'X-API-Key',
            'header_value': ['key1', 'key2', 'key3']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Should match with any of the valid keys
        self.assertTrue(rule.validate('/test', {'X-API-Key': 'key1'}))
        self.assertTrue(rule.validate('/test', {'X-API-Key': 'key2'}))
        self.assertTrue(rule.validate('/test', {'X-API-Key': 'key3'}))
        
        # Should not match with invalid key
        self.assertFalse(rule.validate('/test', {'X-API-Key': 'key4'}))

    def test_user_agent_validation(self):
        """Test user agent validation."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'user_agent': 'MyApp/1.0'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Should match with correct user agent
        headers = {'User-Agent': 'MyApp/1.0'}
        self.assertTrue(rule.validate('/test', headers))
        
        # Should not match with wrong user agent
        headers = {'User-Agent': 'OtherApp/1.0'}
        self.assertFalse(rule.validate('/test', headers))

    def test_eol_validation(self):
        """Test expiration date validation."""
        # Test with future date
        future_date = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'eol': future_date
        }
        rule = RuleValidator(rule_data, 'test_rule')
        self.assertTrue(rule.validate('/test', {}))
        
        # Test with past date
        past_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'eol': past_date
        }
        rule = RuleValidator(rule_data, 'test_rule')
        self.assertFalse(rule.validate('/test', {}))

    def test_source_ip_validation_ipv4(self):
        """Test IPv4 source IP validation."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': '192.168.1.0/24'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Should match IP in range
        client_address = ('192.168.1.100', 12345)
        self.assertTrue(rule.validate('/test', {}, client_address))
        
        # Should not match IP outside range
        client_address = ('10.0.0.1', 12345)
        self.assertFalse(rule.validate('/test', {}, client_address))

    def test_source_ip_validation_x_forwarded_for(self):
        """Test source IP validation with X-Forwarded-For header."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': '10.0.0.0/24'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # X-Forwarded-For should take precedence
        headers = {'X-Forwarded-For': '10.0.0.50'}
        client_address = ('192.168.1.100', 12345)
        self.assertTrue(rule.validate('/test', headers, client_address))
        
        # Should fail if X-Forwarded-For IP not in range
        headers = {'X-Forwarded-For': '192.168.1.50'}
        self.assertFalse(rule.validate('/test', headers, client_address))

    def test_serve_once_flag(self):
        """Test serve_once flag is correctly set."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'serve_once': True
        }
        rule = RuleValidator(rule_data, 'test_rule')
        self.assertTrue(rule.serve_once)
        
        # Default should be False
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        self.assertFalse(rule.serve_once)


class TestIntegration(unittest.TestCase):
    """Integration tests with actual project files."""

    def test_load_project_config(self):
        """Test loading the actual project config.yaml."""
        config_path = 'config.yaml'
        if os.path.exists(config_path):
            config = load_config(config_path)
            self.assertIn('hostname', config)
            self.assertIn('listen_port', config)

    def test_load_project_rules(self):
        """Test loading actual project rules."""
        rules_dir = 'rules'
        if os.path.isdir(rules_dir):
            rules = load_rules(rules_dir)
            # Should have some rules loaded
            self.assertGreater(len(rules), 0)
            # Each rule should have required fields
            for rule in rules:
                self.assertIsNotNone(rule.path)
                self.assertIsNotNone(rule.request_uri)


if __name__ == '__main__':
    unittest.main()
