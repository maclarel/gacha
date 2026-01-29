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
import time
import threading

# Add parent directory to path to import gacha
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gacha import load_config, load_rules, RuleValidator, RuleFileMonitor


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

    def test_source_ip_validation_x_forwarded_for_legacy(self):
        """Test legacy behavior: X-Forwarded-For is ignored without use_xff config."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': '10.0.0.0/24'
        }
        rule = RuleValidator(rule_data, 'test_rule')
        
        # Without use_xff config, X-Forwarded-For should be ignored
        # Only direct connection IP should be checked
        headers = {'X-Forwarded-For': '10.0.0.50'}
        client_address = ('192.168.1.100', 12345)
        config = {'use_xff': False}
        self.assertFalse(rule.validate('/test', headers, client_address, config))
        
        # Direct connection from valid IP should work
        client_address = ('10.0.0.50', 12345)
        self.assertTrue(rule.validate('/test', headers, client_address, config))

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


class TestRuleFileMonitor(unittest.TestCase):
    """Test rule file monitoring and reloading."""
    
    def setUp(self):
        """Create temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
        self.rules_dir = os.path.join(self.test_dir, 'rules')
        os.makedirs(self.rules_dir)
        self.monitors = []  # Track monitors to clean up
    
    def tearDown(self):
        """Clean up temporary directory and stop any running monitors."""
        # Stop all monitors
        for monitor in self.monitors:
            try:
                monitor.stop()
            except Exception:
                pass
        
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def create_rule_file(self, filename: str, rule_data: dict):
        """Helper to create a rule file."""
        rule_path = os.path.join(self.rules_dir, filename)
        with open(rule_path, 'w') as f:
            yaml.dump({'rule': rule_data}, f)
        return rule_path
    
    def test_monitor_initialization(self):
        """Test that monitor initializes correctly."""
        monitor = RuleFileMonitor(self.rules_dir, poll_interval=1.0)
        self.assertEqual(monitor.rules_dir, self.rules_dir)
        self.assertEqual(monitor.poll_interval, 1.0)
        self.assertEqual(len(monitor.rules), 0)
    
    def test_get_rule_files(self):
        """Test getting rule files and their modification times."""
        # Create a rule file
        self.create_rule_file('test1.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        monitor = RuleFileMonitor(self.rules_dir)
        rule_files = monitor.get_rule_files()
        
        self.assertEqual(len(rule_files), 1)
        self.assertIn(os.path.join(self.rules_dir, 'test1.yaml'), rule_files)
    
    def test_has_changes_detects_new_file(self):
        """Test that has_changes detects new rule files."""
        monitor = RuleFileMonitor(self.rules_dir)
        monitor.file_mtimes = monitor.get_rule_files()
        
        # Initially no changes
        self.assertFalse(monitor.has_changes())
        
        # Add a new file
        self.create_rule_file('new.yaml', {
            'path': 'files/new.txt',
            'request_uri': '/new'
        })
        
        # Should detect change
        self.assertTrue(monitor.has_changes())
    
    def test_has_changes_detects_modified_file(self):
        """Test that has_changes detects modified rule files."""
        # Create initial file
        rule_path = self.create_rule_file('test.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        monitor = RuleFileMonitor(self.rules_dir)
        monitor.file_mtimes = monitor.get_rule_files()
        
        # Sleep briefly to ensure mtime changes
        time.sleep(0.1)
        
        # Modify the file
        with open(rule_path, 'w') as f:
            yaml.dump({'rule': {
                'path': 'files/modified.txt',
                'request_uri': '/modified'
            }}, f)
        
        # Should detect change
        self.assertTrue(monitor.has_changes())
    
    def test_has_changes_detects_deleted_file(self):
        """Test that has_changes detects deleted rule files."""
        # Create initial file
        rule_path = self.create_rule_file('test.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        monitor = RuleFileMonitor(self.rules_dir)
        monitor.file_mtimes = monitor.get_rule_files()
        
        # Delete the file
        os.remove(rule_path)
        
        # Should detect change
        self.assertTrue(monitor.has_changes())
    
    def test_reload_rules(self):
        """Test that reload_rules updates the rules list."""
        # Create initial rules
        self.create_rule_file('rule1.yaml', {
            'path': 'files/file1.txt',
            'request_uri': '/file1'
        })
        
        monitor = RuleFileMonitor(self.rules_dir)
        monitor.reload_rules()
        
        # Should have loaded 1 rule
        self.assertEqual(len(monitor.rules), 1)
        self.assertEqual(monitor.rules[0].request_uri, '/file1')
        
        # Add another rule
        self.create_rule_file('rule2.yaml', {
            'path': 'files/file2.txt',
            'request_uri': '/file2'
        })
        
        monitor.reload_rules()
        
        # Should have loaded 2 rules
        self.assertEqual(len(monitor.rules), 2)
    
    def test_get_rules_thread_safe(self):
        """Test that get_rules returns a thread-safe copy."""
        self.create_rule_file('test.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        monitor = RuleFileMonitor(self.rules_dir)
        monitor.reload_rules()
        
        rules1 = monitor.get_rules()
        rules2 = monitor.get_rules()
        
        # Should return copies, not the same list
        self.assertIsNot(rules1, rules2)
        self.assertEqual(len(rules1), len(rules2))
    
    def test_monitor_thread_lifecycle(self):
        """Test starting and stopping the monitor thread."""
        self.create_rule_file('test.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        initial_rules = load_rules(self.rules_dir)
        
        monitor = RuleFileMonitor(self.rules_dir, poll_interval=0.5)
        self.monitors.append(monitor)
        monitor.start(initial_rules)
        
        # Thread should be running
        self.assertIsNotNone(monitor.monitor_thread)
        self.assertTrue(monitor.monitor_thread.is_alive())
        
        # Stop the monitor
        monitor.stop()
        
        # Thread should be stopped (either stop_event is set or thread is not alive)
        self.assertTrue(monitor.stop_event.is_set() or not monitor.monitor_thread.is_alive())
    
    def test_reload_callback_called(self):
        """Test that reload callback is called when rules are reloaded."""
        self.create_rule_file('test.yaml', {
            'path': 'files/test.txt',
            'request_uri': '/test'
        })
        
        callback_called = threading.Event()
        callback_rules = []
        callback_changed_files = []
        
        def test_callback(new_rules, changed_files):
            callback_rules.extend(new_rules)
            callback_changed_files.extend(changed_files)
            callback_called.set()
        
        initial_rules = load_rules(self.rules_dir)
        
        monitor = RuleFileMonitor(self.rules_dir, poll_interval=0.3)
        self.monitors.append(monitor)
        monitor.start(initial_rules, test_callback)
        
        # Add a new rule file
        time.sleep(0.2)  # Give monitor time to start
        self.create_rule_file('new.yaml', {
            'path': 'files/new.txt',
            'request_uri': '/new'
        })
        
        # Wait for callback with longer timeout for CI environments
        callback_called.wait(timeout=3.0)
        
        # Stop monitor
        monitor.stop()
        
        # Callback should have been called with new rules
        self.assertTrue(callback_called.is_set(), "Callback was not called within timeout")
        self.assertEqual(len(callback_rules), 2)
        # Should report new.yaml as changed
        self.assertIn('new.yaml', callback_changed_files)
    
    def test_selective_serve_once_reset(self):
        """Test that serve_once tracking is only reset for changed rules."""
        # Create two rule files
        self.create_rule_file('rule1.yaml', {
            'path': 'files/file1.txt',
            'request_uri': '/file1'
        })
        self.create_rule_file('rule2.yaml', {
            'path': 'files/file2.txt',
            'request_uri': '/file2'
        })
        
        changed_files_list = []
        
        def track_changes(new_rules, changed_files):
            changed_files_list.clear()
            changed_files_list.extend(changed_files)
        
        initial_rules = load_rules(self.rules_dir)
        
        monitor = RuleFileMonitor(self.rules_dir, poll_interval=0.3)
        self.monitors.append(monitor)
        monitor.start(initial_rules, track_changes)
        
        time.sleep(0.2)  # Let monitor start
        
        # Modify only rule1.yaml
        time.sleep(0.1)  # Ensure mtime changes
        self.create_rule_file('rule1.yaml', {
            'path': 'files/file1_modified.txt',
            'request_uri': '/file1-modified'
        })
        
        # Wait for reload
        time.sleep(1.0)
        
        # Stop monitor
        monitor.stop()
        
        # Only rule1.yaml should be in changed files
        self.assertIn('rule1.yaml', changed_files_list)
        self.assertNotIn('rule2.yaml', changed_files_list)


class TestXForwardedForConfiguration(unittest.TestCase):
    """Test X-Forwarded-For configuration validation."""
    
    def setUp(self):
        """Create temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_config_xff_disabled_by_default(self):
        """Test that use_xff is disabled by default."""
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
        self.assertFalse(config.get('use_xff', False))
    
    def test_config_xff_enabled_without_upstream_ip_fails(self):
        """Test that enabling use_xff without xff_upstream_ip raises error."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'hostname': 'localhost',
                'listen_port': 8080,
                'use_xff': True
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        with self.assertRaises(ValueError) as context:
            load_config(config_path)
        self.assertIn('xff_upstream_ip', str(context.exception))
    
    def test_config_xff_enabled_with_empty_upstream_ip_fails(self):
        """Test that enabling use_xff with empty xff_upstream_ip raises error."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'hostname': 'localhost',
                'listen_port': 8080,
                'use_xff': True,
                'xff_upstream_ip': ''
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        with self.assertRaises(ValueError) as context:
            load_config(config_path)
        self.assertIn('xff_upstream_ip', str(context.exception))
    
    def test_config_xff_enabled_with_upstream_ip_succeeds(self):
        """Test that enabling use_xff with xff_upstream_ip succeeds."""
        config_path = os.path.join(self.test_dir, 'config.yaml')
        config_data = {
            'config': {
                'hostname': 'localhost',
                'listen_port': 8080,
                'use_xff': True,
                'xff_upstream_ip': '10.0.1.2'
            }
        }
        with open(config_path, 'w') as f:
            yaml.dump(config_data, f)
        
        config = load_config(config_path)
        self.assertTrue(config['use_xff'])
        self.assertEqual(config['xff_upstream_ip'], '10.0.1.2')


class TestScenario1XFFDisabled(unittest.TestCase):
    """Test Scenario 1: X_FORWARDED_FOR is set to False."""
    
    def test_scenario1_direct_requests_from_valid_ips(self):
        """Test that direct requests from valid IPs succeed."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.2', '10.0.1.3']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {'use_xff': False}
        
        # Request from 10.0.1.2 should succeed
        client_address = ('10.0.1.2', 12345)
        self.assertTrue(rule.validate('/test', {}, client_address, config))
        
        # Request from 10.0.1.3 should succeed
        client_address = ('10.0.1.3', 12345)
        self.assertTrue(rule.validate('/test', {}, client_address, config))
    
    def test_scenario1_direct_requests_from_invalid_ips(self):
        """Test that direct requests from invalid IPs fail."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.2', '10.0.1.3']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {'use_xff': False}
        
        # Request from 10.0.1.4 should fail
        client_address = ('10.0.1.4', 12345)
        self.assertFalse(rule.validate('/test', {}, client_address, config))
    
    def test_scenario1_xff_header_is_ignored(self):
        """Test that X-Forwarded-For header is ignored when use_xff is False."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.2', '10.0.1.3']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {'use_xff': False}
        
        # Request from invalid IP with X-Forwarded-For header should fail
        headers = {'X-Forwarded-For': '10.0.1.2'}
        client_address = ('192.168.1.100', 12345)
        self.assertFalse(rule.validate('/test', headers, client_address, config))


class TestScenario3XFFEnabled(unittest.TestCase):
    """Test Scenario 3: X_FORWARDED_FOR is set to True with XFF_UPSTREAM_IP set."""
    
    def test_scenario3_upstream_without_xff_header_fails(self):
        """Test that requests from upstream without X-Forwarded-For header fail."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9', '10.0.1.10']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.2'
        }
        
        # Request from upstream without X-Forwarded-For should fail
        client_address = ('10.0.1.2', 12345)
        self.assertFalse(rule.validate('/test', {}, client_address, config))
    
    def test_scenario3_upstream_with_invalid_xff_fails(self):
        """Test that requests from upstream with invalid X-Forwarded-For fail."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9', '10.0.1.10']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.2'
        }
        
        # Request from upstream with invalid X-Forwarded-For should fail
        headers = {'X-Forwarded-For': '10.0.1.5'}
        client_address = ('10.0.1.2', 12345)
        self.assertFalse(rule.validate('/test', headers, client_address, config))
    
    def test_scenario3_upstream_with_valid_xff_succeeds(self):
        """Test that requests from upstream with valid X-Forwarded-For succeed."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9', '10.0.1.10']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.2'
        }
        
        # Request from upstream with valid X-Forwarded-For should succeed
        headers = {'X-Forwarded-For': '10.0.1.9'}
        client_address = ('10.0.1.2', 12345)
        self.assertTrue(rule.validate('/test', headers, client_address, config))
        
        # Request from upstream with another valid X-Forwarded-For should succeed
        headers = {'X-Forwarded-For': '10.0.1.10'}
        self.assertTrue(rule.validate('/test', headers, client_address, config))
    
    def test_scenario3_direct_from_valid_ips_succeeds(self):
        """Test that direct requests from valid IPs succeed regardless of X-Forwarded-For."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9', '10.0.1.10']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.2'
        }
        
        # Direct request from 10.0.1.9 should succeed
        client_address = ('10.0.1.9', 12345)
        self.assertTrue(rule.validate('/test', {}, client_address, config))
        
        # Direct request from 10.0.1.10 should succeed
        client_address = ('10.0.1.10', 12345)
        self.assertTrue(rule.validate('/test', {}, client_address, config))
        
        # Direct request with X-Forwarded-For header should also succeed
        headers = {'X-Forwarded-For': '192.168.1.1'}
        self.assertTrue(rule.validate('/test', headers, client_address, config))
    
    def test_scenario3_xff_upstream_with_cidr_notation(self):
        """Test that xff_upstream_ip supports CIDR notation."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.0/24'  # CIDR notation
        }
        
        # Request from IP in upstream CIDR with valid X-Forwarded-For should succeed
        headers = {'X-Forwarded-For': '10.0.1.9'}
        client_address = ('10.0.1.2', 12345)
        self.assertTrue(rule.validate('/test', headers, client_address, config))
        
        # Request from IP outside upstream CIDR should use direct connection IP
        client_address = ('10.0.2.1', 12345)
        self.assertFalse(rule.validate('/test', headers, client_address, config))
    
    def test_scenario3_multiple_ips_in_xff_header(self):
        """Test that X-Forwarded-For with multiple IPs uses the first one."""
        rule_data = {
            'path': 'files/test.txt',
            'request_uri': '/test',
            'source_ip': ['10.0.1.9']
        }
        rule = RuleValidator(rule_data, 'test_rule')
        config = {
            'use_xff': True,
            'xff_upstream_ip': '10.0.1.2'
        }
        
        # X-Forwarded-For with multiple IPs should use the first one
        headers = {'X-Forwarded-For': '10.0.1.9, 192.168.1.1, 172.16.0.1'}
        client_address = ('10.0.1.2', 12345)
        self.assertTrue(rule.validate('/test', headers, client_address, config))


if __name__ == '__main__':
    unittest.main()

