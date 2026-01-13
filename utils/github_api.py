#!/usr/bin/env python3
"""
GitHub API 封装模块
用于更新 GitHub Environment Secrets
"""

import base64
import os

import httpx
from nacl import encoding, public


class GitHubAPI:
	"""GitHub API 客户端"""

	def __init__(self, token: str | None = None):
		self.token = token or os.getenv('GH_PAT')
		if not self.token:
			raise ValueError('GitHub token is required. Set GH_PAT environment variable.')

		self.base_url = 'https://api.github.com'
		self.headers = {
			'Accept': 'application/vnd.github+json',
			'Authorization': f'Bearer {self.token}',
			'X-GitHub-Api-Version': '2022-11-28',
		}

	def _encrypt_secret(self, public_key: str, secret_value: str) -> str:
		"""使用 libsodium 加密 secret 值

		Args:
			public_key: Base64 编码的公钥
			secret_value: 要加密的明文值

		Returns:
			Base64 编码的加密值
		"""
		public_key_bytes = public.PublicKey(public_key.encode('utf-8'), encoding.Base64Encoder())
		sealed_box = public.SealedBox(public_key_bytes)
		encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
		return base64.b64encode(encrypted).decode('utf-8')

	def get_repo_info(self) -> tuple[str, str] | None:
		"""从 git remote 获取仓库信息

		Returns:
			(owner, repo) 元组，失败返回 None
		"""
		repo_str = os.getenv('GITHUB_REPOSITORY')
		if repo_str and '/' in repo_str:
			parts = repo_str.split('/')
			return parts[0], parts[1]

		# 尝试从环境变量获取
		owner = os.getenv('REPO_OWNER')
		repo = os.getenv('REPO_NAME')
		if owner and repo:
			return owner, repo

		return None

	def get_environment_public_key(self, owner: str, repo: str, environment: str) -> dict | None:
		"""获取环境的公钥

		Args:
			owner: 仓库所有者
			repo: 仓库名称
			environment: 环境名称

		Returns:
			包含 key_id 和 key 的字典，失败返回 None
		"""
		url = f'{self.base_url}/repos/{owner}/{repo}/environments/{environment}/secrets/public-key'

		try:
			with httpx.Client(timeout=30.0) as client:
				response = client.get(url, headers=self.headers)

				if response.status_code == 200:
					data = response.json()
					return {'key_id': data['key_id'], 'key': data['key']}
				else:
					print(f'[ERROR] Failed to get environment public key: HTTP {response.status_code}')
					print(f'[ERROR] Response: {response.text}')
					return None
		except Exception as e:
			print(f'[ERROR] Failed to get environment public key: {e}')
			return None

	def update_environment_secret(
		self,
		owner: str,
		repo: str,
		environment: str,
		secret_name: str,
		secret_value: str,
	) -> bool:
		"""更新环境 Secret

		Args:
			owner: 仓库所有者
			repo: 仓库名称
			environment: 环境名称
			secret_name: Secret 名称
			secret_value: Secret 值（明文）

		Returns:
			成功返回 True，失败返回 False
		"""
		# 获取公钥
		key_info = self.get_environment_public_key(owner, repo, environment)
		if not key_info:
			return False

		# 加密 secret 值
		encrypted_value = self._encrypt_secret(key_info['key'], secret_value)

		# 更新 secret
		url = f'{self.base_url}/repos/{owner}/{repo}/environments/{environment}/secrets/{secret_name}'

		payload = {
			'encrypted_value': encrypted_value,
			'key_id': key_info['key_id'],
		}

		try:
			with httpx.Client(timeout=30.0) as client:
				response = client.put(url, headers=self.headers, json=payload)

				if response.status_code in (201, 204):
					print(f'[SUCCESS] Secret "{secret_name}" updated successfully')
					return True
				else:
					print(f'[ERROR] Failed to update secret: HTTP {response.status_code}')
					print(f'[ERROR] Response: {response.text}')
					return False
		except Exception as e:
			print(f'[ERROR] Failed to update secret: {e}')
			return False


def update_anyrouter_accounts(accounts_json: str, environment: str = 'production') -> bool:
	"""便捷函数：更新 ANYROUTER_ACCOUNTS secret

	Args:
		accounts_json: 新的账号配置 JSON 字符串
		environment: 环境名称，默认 production

	Returns:
		成功返回 True，失败返回 False
	"""
	try:
		api = GitHubAPI()
		repo_info = api.get_repo_info()

		if not repo_info:
			print('[ERROR] Cannot determine repository info. Set GITHUB_REPOSITORY or REPO_OWNER/REPO_NAME.')
			return False

		owner, repo = repo_info
		print(f'[INFO] Updating secret for {owner}/{repo} in environment "{environment}"')

		return api.update_environment_secret(owner, repo, environment, 'ANYROUTER_ACCOUNTS', accounts_json)
	except Exception as e:
		print(f'[ERROR] Failed to update ANYROUTER_ACCOUNTS: {e}')
		return False
