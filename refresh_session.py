#!/usr/bin/env python3
"""
AnyRouter Session 自动刷新脚本

当签到失败（session 过期）时，自动登录获取新 session 并更新 GitHub Environment Secret。
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from utils.config import AppConfig, CredentialConfig, load_credentials_config
from utils.github_api import update_anyrouter_accounts
from utils.notify import notify

load_dotenv()


async def auto_login_and_get_session(
	credential: CredentialConfig,
	provider_config,
	account_index: int,
) -> str | None:
	"""使用 Playwright 自动登录并获取 session cookie

	Args:
		credential: 账号凭证配置
		provider_config: Provider 配置
		account_index: 账号索引

	Returns:
		session cookie 值，失败返回 None
	"""
	account_name = credential.get_display_name(account_index)
	login_url = f'{provider_config.domain}{provider_config.login_path}'

	print(f'[PROCESSING] {account_name}: Starting auto-login...')

	async with async_playwright() as p:
		with tempfile.TemporaryDirectory() as temp_dir:
			context = await p.chromium.launch_persistent_context(
				user_data_dir=temp_dir,
				headless=False,
				user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
				viewport={'width': 1920, 'height': 1080},
				args=[
					'--disable-blink-features=AutomationControlled',
					'--disable-dev-shm-usage',
					'--disable-web-security',
					'--disable-features=VizDisplayCompositor',
					'--no-sandbox',
				],
			)

			page = await context.new_page()

			try:
				# 访问登录页面
				print(f'[PROCESSING] {account_name}: Navigating to login page...')
				await page.goto(login_url, wait_until='networkidle')

				# 等待页面完全加载
				try:
					await page.wait_for_function('document.readyState === "complete"', timeout=10000)
				except Exception:
					await page.wait_for_timeout(3000)

				# 尝试多种选择器来定位输入框
				username_selectors = [
					'input[type="email"]',
					'input[name="username"]',
					'input[name="email"]',
					'input[placeholder*="邮箱"]',
					'input[placeholder*="email"]',
					'input[placeholder*="用户名"]',
					'input[id*="username"]',
					'input[id*="email"]',
				]

				password_selectors = [
					'input[type="password"]',
					'input[name="password"]',
					'input[placeholder*="密码"]',
					'input[placeholder*="password"]',
					'input[id*="password"]',
				]

				submit_selectors = [
					'button[type="submit"]',
					'button:has-text("登录")',
					'button:has-text("Login")',
					'button:has-text("Sign in")',
					'input[type="submit"]',
					'.login-btn',
					'.submit-btn',
				]

				# 填写用户名
				username_filled = False
				for selector in username_selectors:
					try:
						element = await page.query_selector(selector)
						if element:
							await element.fill(credential.username)
							username_filled = True
							print(f'[INFO] {account_name}: Username filled using selector: {selector}')
							break
					except Exception:
						continue

				if not username_filled:
					print(f'[FAILED] {account_name}: Could not find username input field')
					await context.close()
					return None

				# 填写密码
				password_filled = False
				for selector in password_selectors:
					try:
						element = await page.query_selector(selector)
						if element:
							await element.fill(credential.password)
							password_filled = True
							print(f'[INFO] {account_name}: Password filled using selector: {selector}')
							break
					except Exception:
						continue

				if not password_filled:
					print(f'[FAILED] {account_name}: Could not find password input field')
					await context.close()
					return None

				# 点击登录按钮
				submit_clicked = False
				for selector in submit_selectors:
					try:
						element = await page.query_selector(selector)
						if element:
							await element.click()
							submit_clicked = True
							print(f'[INFO] {account_name}: Submit button clicked using selector: {selector}')
							break
					except Exception:
						continue

				if not submit_clicked:
					# 尝试按 Enter 键提交
					print(f'[INFO] {account_name}: No submit button found, trying Enter key...')
					await page.keyboard.press('Enter')

				# 等待登录完成
				print(f'[PROCESSING] {account_name}: Waiting for login to complete...')

				# 等待页面跳转或 session cookie 出现
				try:
					# 等待 URL 变化（登录成功后通常会跳转）
					await page.wait_for_url(
						lambda url: '/login' not in url.lower(),
						timeout=15000,
					)
				except Exception:
					# 如果 URL 没变化，等待一段时间检查 cookies
					await page.wait_for_timeout(5000)

				# 提取 cookies
				cookies = await page.context.cookies()

				session_cookie = None
				for cookie in cookies:
					if cookie.get('name') == 'session':
						session_cookie = cookie.get('value')
						break

				if session_cookie:
					print(f'[SUCCESS] {account_name}: Successfully obtained new session cookie')
					await context.close()
					return session_cookie
				else:
					# 检查是否有错误提示
					error_selectors = ['.error', '.alert-danger', '.error-message', '[class*="error"]']
					for selector in error_selectors:
						try:
							error_element = await page.query_selector(selector)
							if error_element:
								error_text = await error_element.text_content()
								if error_text:
									print(f'[FAILED] {account_name}: Login error - {error_text.strip()[:100]}')
									break
						except Exception:
							continue

					print(f'[FAILED] {account_name}: Could not find session cookie after login')
					await context.close()
					return None

			except Exception as e:
				print(f'[FAILED] {account_name}: Error during auto-login - {str(e)[:100]}')
				await context.close()
				return None


async def refresh_all_sessions() -> bool:
	"""刷新所有账号的 session

	Returns:
		成功返回 True，失败返回 False
	"""
	print('[SYSTEM] AnyRouter Session Refresh Script Started')
	print(f'[TIME] Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

	# 加载配置
	app_config = AppConfig.load_from_env()
	credentials = load_credentials_config()

	if not credentials:
		print('[FAILED] Unable to load credentials configuration')
		return False

	print(f'[INFO] Found {len(credentials)} credential configurations')

	# 逐个账号登录获取新 session
	new_accounts = []
	success_count = 0
	failed_accounts = []

	for i, credential in enumerate(credentials):
		account_name = credential.get_display_name(i)

		# 获取 provider 配置
		provider_config = app_config.get_provider(credential.provider)
		if not provider_config:
			print(f'[FAILED] {account_name}: Provider "{credential.provider}" not found')
			failed_accounts.append(account_name)
			continue

		# 自动登录获取新 session
		new_session = await auto_login_and_get_session(credential, provider_config, i)

		if new_session:
			# 创建新的 AccountConfig
			account_config = credential.to_account_config(new_session)
			new_accounts.append(account_config.to_dict())
			success_count += 1
			print(f'[SUCCESS] {account_name}: Session refreshed successfully')
		else:
			failed_accounts.append(account_name)
			print(f'[FAILED] {account_name}: Failed to refresh session')

		# 账号之间稍微等待，避免触发限制
		if i < len(credentials) - 1:
			await asyncio.sleep(2)

	# 检查结果
	total_count = len(credentials)
	print(f'\n[STATS] Session refresh completed: {success_count}/{total_count} successful')

	if success_count == 0:
		print('[ERROR] All accounts failed to refresh session')
		# 发送通知
		notify.push_message(
			'AnyRouter Session Refresh Failed',
			f'All {total_count} accounts failed to refresh session.\nFailed accounts: {", ".join(failed_accounts)}',
			msg_type='text',
		)
		return False

	# 构建新的 ANYROUTER_ACCOUNTS JSON
	accounts_json = json.dumps(new_accounts, ensure_ascii=False, separators=(',', ':'))

	print(f'[INFO] New ANYROUTER_ACCOUNTS JSON generated ({len(accounts_json)} characters)')

	# 更新 GitHub Secret
	print('[PROCESSING] Updating GitHub Environment Secret...')

	if update_anyrouter_accounts(accounts_json):
		print('[SUCCESS] GitHub Secret updated successfully')

		# 发送成功通知
		if failed_accounts:
			notify.push_message(
				'AnyRouter Session Refresh Partial Success',
				f'Session refresh completed: {success_count}/{total_count} successful.\n'
				f'Failed accounts: {", ".join(failed_accounts)}\n'
				f'GitHub Secret has been updated.',
				msg_type='text',
			)
		else:
			notify.push_message(
				'AnyRouter Session Refresh Success',
				f'All {total_count} accounts session refreshed successfully.\n'
				f'GitHub Secret has been updated.',
				msg_type='text',
			)

		return True
	else:
		print('[FAILED] Failed to update GitHub Secret')
		notify.push_message(
			'AnyRouter Session Refresh Failed',
			f'Session obtained for {success_count}/{total_count} accounts, '
			f'but failed to update GitHub Secret.\n'
			f'Please check GH_PAT token permissions.',
			msg_type='text',
		)
		return False


def run_main():
	"""运行主函数的包装函数"""
	try:
		success = asyncio.run(refresh_all_sessions())
		sys.exit(0 if success else 1)
	except KeyboardInterrupt:
		print('\n[WARNING] Program interrupted by user')
		sys.exit(1)
	except Exception as e:
		print(f'\n[FAILED] Error occurred during program execution: {e}')
		sys.exit(1)


if __name__ == '__main__':
	run_main()
