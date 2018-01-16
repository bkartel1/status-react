import pytest
import sys
import re
import subprocess
import time
import asyncio

from tests import bit_bar_api_key
from os import environ
from appium import webdriver
from abc import ABCMeta, abstractmethod
from selenium.common.exceptions import WebDriverException
from tests import test_suite_data, start_threads
from views.base_view import BaseView
from api_bindings.bitbar import BitBar


class AbstractTestCase:

    __metaclass__ = ABCMeta

    @property
    def sauce_username(self):
        return environ.get('SAUCE_USERNAME')

    @property
    def sauce_access_key(self):
        return environ.get('SAUCE_ACCESS_KEY')

    @property
    def executor_sauce_lab(self):
        return 'http://%s:%s@ondemand.saucelabs.com:80/wd/hub' % (self.sauce_username, self.sauce_access_key)

    @property
    def executor_local(self):
        return 'http://localhost:4723/wd/hub'

    def print_sauce_lab_info(self, driver):
        sys.stdout = sys.stderr
        print("SauceOnDemandSessionID=%s job-name=%s" % (driver.session_id,
                                                         pytest.config.getoption('build')))

    def add_local_devices_to_capabilities(self):
        updated_capabilities = list()
        raw_out = re.split(r'[\r\\n]+', str(subprocess.check_output(['adb', 'devices'])).rstrip())
        for line in raw_out[1:]:
            serial = re.findall(r"([\d.\d:]*\d+)", line)
            if serial:
                capabilities = self.capabilities_local
                capabilities['udid'] = serial[0]
                updated_capabilities.append(capabilities)
        return updated_capabilities

    @property
    def capabilities_sauce_lab(self):
        desired_caps = dict()
        desired_caps['app'] = 'sauce-storage:' + test_suite_data.apk_name

        desired_caps['build'] = pytest.config.getoption('build')
        desired_caps['name'] = test_suite_data.current_test.name
        desired_caps['platformName'] = 'Android'
        desired_caps['appiumVersion'] = '1.7.2'
        desired_caps['platformVersion'] = '6.0'
        desired_caps['deviceName'] = 'Android GoogleAPI Emulator'
        desired_caps['deviceOrientation'] = "portrait"
        desired_caps['commandTimeout'] = 600
        desired_caps['idleTimeout'] = 1000
        desired_caps['unicodeKeyboard'] = True
        return desired_caps

    @property
    def capabilities_local(self):
        desired_caps = dict()
        desired_caps['app'] = pytest.config.getoption('apk')
        desired_caps['deviceName'] = 'nexus_5'
        desired_caps['platformName'] = 'Android'
        desired_caps['appiumVersion'] = '1.7.2'
        desired_caps['platformVersion'] = '6.0'
        desired_caps['newCommandTimeout'] = 600
        desired_caps['fullReset'] = False
        desired_caps['unicodeKeyboard'] = True
        return desired_caps

    @abstractmethod
    def setup_method(self, method):
        raise NotImplementedError('Should be overridden from a child class')

    @abstractmethod
    def teardown_method(self, method):
        raise NotImplementedError('Should be overridden from a child class')

    @property
    def environment(self):
        return pytest.config.getoption('env')

    @property
    def implicitly_wait(self):
        return 8

    errors = []

    def verify_no_errors(self):
        if self.errors:
            pytest.fail('. '.join([self.errors.pop(0) for _ in range(len(self.errors))]))


class SingleDeviceTestCase(AbstractTestCase):

    def setup_method(self, method):
        capabilities = {'local': {'executor': self.executor_local,
                                  'capabilities': self.capabilities_local},
                        'sauce': {'executor': self.executor_sauce_lab,
                                  'capabilities': self.capabilities_sauce_lab}}
        counter = 0
        self.driver = None
        while not self.driver and counter <= 3:
            try:
                self.driver = webdriver.Remote(capabilities[self.environment]['executor'],
                                               capabilities[self.environment]['capabilities'])
                self.driver.implicitly_wait(self.implicitly_wait)
                BaseView(self.driver).accept_agreements()
                test_suite_data.current_test.jobs.append(self.driver.session_id)
                break
            except WebDriverException:
                counter += 1

    def teardown_method(self, method):
        if self.environment == 'sauce':
            self.print_sauce_lab_info(self.driver)
        try:
            self.driver.quit()
        except (WebDriverException, AttributeError):
            pass


class LocalMultipleDeviceTestCase(AbstractTestCase):

    def setup_method(self, method):
        self.drivers = dict()

    def create_drivers(self, quantity):
        capabilities = self.add_local_devices_to_capabilities()
        for driver in range(quantity):
            self.drivers[driver] = webdriver.Remote(self.executor_local, capabilities[driver])
            self.drivers[driver].implicitly_wait(self.implicitly_wait)
            BaseView(self.drivers[driver]).accept_agreements()
            test_suite_data.current_test.jobs.append(self.drivers[driver].session_id)

    def teardown_method(self, method):
        for driver in self.drivers:
            try:
                self.drivers[driver].quit()
            except WebDriverException:
                pass


class SauceMultipleDeviceTestCase(AbstractTestCase):

    @classmethod
    def setup_class(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    def setup_method(self, method):
        self.drivers = dict()

    def create_drivers(self, quantity=2):
        self.drivers = self.loop.run_until_complete(start_threads(quantity, webdriver.Remote,
                                                    self.drivers,
                                                    self.executor_sauce_lab,
                                                    self.capabilities_sauce_lab))
        for driver in range(quantity):
            self.drivers[driver].implicitly_wait(self.implicitly_wait)
            BaseView(self.drivers[driver]).accept_agreements()
            test_suite_data.current_test.jobs.append(self.drivers[driver].session_id)

    def teardown_method(self, method):
        for driver in self.drivers:
            try:
                self.print_sauce_lab_info(self.drivers[driver])
                self.drivers[driver].quit()
            except (WebDriverException, AttributeError):
                pass

    @classmethod
    def teardown_class(cls):
        cls.loop.close()


class BitBarTestCase(AbstractTestCase):

    def get_performance_diff(self, previous_build=None):

        # imports are necessary on this level for selecting 'Agg'

        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        bit_bar = BitBar(bit_bar_api_key)
        data = dict()
        for name in test_suite_data.apk_name, previous_build:
            data[name] = dict()
            data[name]['seconds'] = list()
            data[name]['CPU'] = list()
            data[name]['RAM'] = list()
            try:
                build_data = bit_bar.get_performance_by(name, test_suite_data.current_test.name)
            except BitBar.NameNotFoundException:
                build_data = None
            if build_data:
                for second, nothing in enumerate(build_data):
                    data[name]['seconds'].append(second)
                    data[name]['CPU'].append(nothing['cpuUsage'] * 100)
                    data[name]['RAM'].append(float(nothing['memUsage']) / 1000000)
        plt.style.use('dark_background')
        for i in 'CPU', 'RAM':
            fig, ax = plt.subplots(nrows=1, ncols=1, figsize=(15, 5))
            ax.plot(data[test_suite_data.apk_name]['seconds'],
                    data[test_suite_data.apk_name][i], 'o-', color='#40e0d0', label=test_suite_data.apk_name)
            if data[previous_build]:
                ax.plot(data[previous_build]['seconds'],
                        data[previous_build][i], 'o-', color='#ffa500', label=previous_build)
            plt.title('diff(%s): ' % i + test_suite_data.current_test.name)
            plt.legend()
            fig.savefig('%s_' % i + test_suite_data.current_test.name + '.png')

    @property
    def capabilities_bitbar(self):
        capabilities = dict()
        capabilities['testdroid_apiKey'] = bit_bar_api_key
        capabilities['testdroid_target'] = 'android'
        capabilities['testdroid_device'] = 'LG Google Nexus 5X 6.0.1'
        capabilities['testdroid_app'] = pytest.config.getoption('apk')
        capabilities['testdroid_project'] = test_suite_data.apk_name
        capabilities['testdroid_testrun'] = test_suite_data.current_test.name
        capabilities['testdroid_gamebench'] = True
        capabilities['testdroid_findDevice'] = True
        capabilities['testdroid_testTimeout'] = 600

        capabilities['platformName'] = 'Android'
        capabilities['deviceName'] = 'Android Phone'
        capabilities['automationName'] = 'Appium'
        capabilities['newCommandTimeout'] = 600
        capabilities['autoDismissAlerts'] = False
        return capabilities

    @property
    def executor_bitbar(self):
        return 'http://appium.testdroid.com/wd/hub'

    def setup_method(self, method):
        self.driver = webdriver.Remote(self.executor_bitbar,
                                       self.capabilities_bitbar)
        self.driver.implicitly_wait(self.implicitly_wait)
        BaseView(self.driver).accept_agreements()
        test_suite_data.current_test.jobs.append(self.driver.session_id)

    def teardown_method(self, method):
        try:
            self.driver.quit()
        except WebDriverException:
            pass
        finally:
            for i in range(4):
                try:
                    self.get_performance_diff(BitBar(bit_bar_api_key).get_previous_project_name())
                    return
                except BitBar.ResponseError:
                    time.sleep(30)


environments = {'bitbar': BitBarTestCase,
                'local': LocalMultipleDeviceTestCase,
                'sauce': SauceMultipleDeviceTestCase}


class MultipleDeviceTestCase(environments[pytest.config.getoption('env')]):

    pass
