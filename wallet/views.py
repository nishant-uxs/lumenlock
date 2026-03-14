from django.shortcuts import render
from django.http import JsonResponse
from stellar_sdk import Asset, Server, Keypair, TransactionBuilder, Network
from stellar_sdk.exceptions import NotFoundError, BadRequestError
from .models import Wallet
import cryptocode
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
import requests
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import logging

logger = logging.getLogger(__name__)

def home(request):
    return render(request, 'home.html')

@login_required
def create_wallet(request):
    if Wallet.objects.filter(user=request.user).exists():
        return redirect('dashboard')
    
    encryption_key = request.POST.get('password')
    if not encryption_key or len(encryption_key) < 8:
        return JsonResponse({
            'status': 'error',
            'message': 'Password must be at least 8 characters long'
        }, status=400)
    
    try:
        keypair = Keypair.random()
        encrypted_secret_seed = cryptocode.encrypt(keypair.secret, encryption_key)
        wallet = Wallet.objects.create(
            user=request.user,
            public_key=keypair.public_key,
            secret_seed=encrypted_secret_seed
        )
        url = "https://friendbot.stellar.org"
        response = requests.get(url, params={"addr": keypair.public_key}, timeout=10)
        
        if response.status_code != 200:
            wallet.delete()
            return JsonResponse({
                'status': 'error',
                'message': 'Failed to fund wallet from friendbot'
            }, status=500)
        
        return redirect('dashboard')
    except Exception as e:
        logger.error(f'Error creating wallet for user {request.user.id}: {str(e)}', exc_info=True)
        if 'wallet' in locals():
            wallet.delete()
        return JsonResponse({
            'status': 'error',
            'message': 'Failed to create wallet. Please try again later.'
        }, status=500)

@login_required
def check_balance(request):
    try:
        wallet = Wallet.objects.filter(user=request.user).first()
        if not wallet:
            return JsonResponse({
                'status': 'error',
                'message': 'No wallet found for user'
            }, status=404)
        
        public_key = wallet.public_key
        
        if not is_valid_stellar_address(public_key):
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid Stellar address'
            }, status=400)
        
        server = Server("https://horizon-testnet.stellar.org")
        server.client.request_timeout = 10
        account = server.accounts().account_id(public_key).call()
        
        balances = account.get('balances', [])
        if not balances:
            return JsonResponse({
                'status': 'error',
                'message': 'No balances found for account'
            }, status=404)
        
        native_balance = None
        for balance in balances:
            if balance.get('asset_type') == 'native':
                native_balance = balance.get('balance', '0')
                break
        
        if native_balance is None:
            return JsonResponse({
                'status': 'error',
                'message': 'Native XLM balance not found'
            }, status=404)
        
        return JsonResponse({
            'status': 'success',
            'balance': native_balance
        })
    except NotFoundError:
        return JsonResponse({
            'status': 'error',
            'message': 'Account not found on the Stellar network'
        }, status=404)
    except Exception as e:
        logger.error(f'Error checking balance for user {request.user.id}: {str(e)}', exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': 'Failed to check balance. Please try again later.'
        }, status=500)

@login_required
def send_money(request):
    if request.method == 'POST':
        try:
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Invalid JSON data'
                }, status=400)
            destination_public_key = data.get('recipient')
            amount = data.get('amount')
            encryption_key = data.get('transaction_password')
            
            if not destination_public_key or not amount or not encryption_key:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Missing required fields'
                }, status=400)
            
            if not is_valid_stellar_address(destination_public_key):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Invalid recipient address'
                }, status=400)
            
            try:
                amount_decimal = Decimal(str(amount))
                if amount_decimal <= 0:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Amount must be greater than 0'
                    }, status=400)
                if amount_decimal.as_tuple().exponent < -7:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Amount cannot have more than 7 decimal places'
                    }, status=400)
                
                quantized_amount = amount_decimal.quantize(Decimal('0.0000001'), rounding=ROUND_DOWN)
                if quantized_amount <= 0:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Amount too small (minimum 0.0000001 XLM)'
                    }, status=400)
                
                amount = str(quantized_amount)
            except (ValueError, InvalidOperation):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Invalid amount format'
                }, status=400)
            
            wallet = Wallet.objects.filter(user=request.user).first()
            if not wallet:
                return JsonResponse({
                    'status': 'error',
                    'message': 'No wallet found for user'
                }, status=404)
            
            server = Server("https://horizon-testnet.stellar.org")
            server.client.request_timeout = 10
            
            decrypted_secret = cryptocode.decrypt(wallet.secret_seed, encryption_key)
            if not decrypted_secret:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Incorrect transaction password'
                }, status=401)
            
            try:
                source_keypair = Keypair.from_secret(decrypted_secret)
            except Exception:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Incorrect transaction password'
                }, status=401)
            
            try:
                source_account = server.load_account(source_keypair.public_key)
            except NotFoundError:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Your wallet account not found. Please ensure it is funded.'
                }, status=404)
            
            try:
                destination_account = server.load_account(destination_public_key)
            except NotFoundError:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Recipient account not found on Stellar network'
                }, status=404)
            
            transaction = TransactionBuilder(
                source_account=source_account,
                network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
                base_fee=100
            ).append_payment_op(
                destination=destination_public_key,
                amount=amount,
                asset=Asset.native()
            ).set_timeout(30).build()
            
            transaction.sign(source_keypair)
            response = server.submit_transaction(transaction)
            
            return JsonResponse({
                'message': 'Payment sent successfully',
                'status': 'success',
                'transaction_hash': response.get('hash', 'N/A')
            })
        
        except BadRequestError as e:
            logger.warning(f'Transaction failed for user {request.user.id}: {str(e)}')
            return JsonResponse({
                'status': 'error',
                'message': 'Transaction failed. Please check your balance and try again.'
            }, status=400)
        except Exception as e:
            logger.error(f'Error sending payment for user {request.user.id}: {str(e)}', exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': 'Failed to send payment. Please try again later.'
            }, status=500)
    else:
        return JsonResponse({
            'status': 'error',
            'message': 'Method not allowed'
        }, status=405)

def is_valid_stellar_address(address):
    if not address or not isinstance(address, str):
        return False
    if len(address) != 56:
        return False
    if not address.startswith('G'):
        return False
    if not re.match(r'^[A-Z2-7]+$', address):
        return False
    try:
        Keypair.from_public_key(address)
        return True
    except Exception:
        return False

@login_required
def dashboard(request):
    wallet_exists = Wallet.objects.filter(user=request.user).exists()
    if not wallet_exists:
        return render(request, 'dashboard.html', {'wallet_exists': wallet_exists})
    
    wallet = Wallet.objects.filter(user=request.user).first()
    if not wallet:
        return render(request, 'dashboard.html', {'wallet_exists': False})
    
    try:
        server = Server("https://horizon-testnet.stellar.org")
        server.client.request_timeout = 10
        account = server.accounts().account_id(wallet.public_key).call()
        
        balances = account.get('balances', [])
        native_balance = '0'
        for balance in balances:
            if balance.get('asset_type') == 'native':
                native_balance = balance.get('balance', '0')
                break
        
        context = {
            'wallet_exists': True,
            'balance': native_balance,
            'public_key': wallet.public_key
        }
    except Exception as e:
        logger.error(f'Error loading dashboard for user {request.user.id}: {str(e)}', exc_info=True)
        context = {
            'wallet_exists': True,
            'balance': '0',
            'public_key': wallet.public_key if wallet else '',
            'error': 'Failed to load wallet data. Please refresh the page.'
        }
    return render(request, 'dashboard.html', context)

@login_required
def transaction_history(request):
    try:
        wallet = Wallet.objects.filter(user=request.user).first()
        if not wallet:
            return JsonResponse({
                'status': 'error',
                'message': 'No wallet found'
            }, status=404)
        
        server = Server("https://horizon-testnet.stellar.org")
        server.client.request_timeout = 10
        
        target_count = 20
        max_pages = 5
        transaction_list = []
        
        call_builder = server.operations().for_account(wallet.public_key).limit(50).order(desc=True)
        page = call_builder.call()
        
        for _ in range(max_pages):
            embedded = page.get('_embedded', {})
            records = embedded.get('records', [])
            
            if not records:
                break
            
            for op in records:
                op_type = op.get('type')
                if op_type == 'payment' or op_type == 'create_account':
                    transaction_list.append({
                        'hash': op.get('transaction_hash', 'N/A'),
                        'created_at': op.get('created_at', ''),
                        'type': op_type,
                        'from': op.get('from', op.get('funder', 'N/A')),
                        'to': op.get('to', op.get('account', 'N/A')),
                        'amount': op.get('amount', op.get('starting_balance', '0')),
                        'asset_type': op.get('asset_type', 'native')
                    })
                    
                    if len(transaction_list) >= target_count:
                        break
            
            if len(transaction_list) >= target_count:
                break
            
            next_link = page.get('_links', {}).get('next', {}).get('href')
            if not next_link:
                break
            
            try:
                response = requests.get(next_link, timeout=10)
                if not response.ok:
                    logger.warning(f'Horizon pagination returned status {response.status_code}')
                    break
                page = response.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                logger.warning(f'Error fetching next page of transactions: {str(e)}')
                break
        
        return JsonResponse({
            'status': 'success',
            'transactions': transaction_list
        })
    
    except NotFoundError:
        return JsonResponse({
            'status': 'error',
            'message': 'Account not found'
        }, status=404)
    except Exception as e:
        logger.error(f'Error fetching transaction history for user {request.user.id}: {str(e)}', exc_info=True)
        return JsonResponse({
            'status': 'error',
            'message': 'Failed to load transaction history. Please try again later.'
        }, status=500)