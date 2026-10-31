/** Bounded draft test; never changes the live mail configuration or echoes credentials. */
export const SMTP_TEST_WORKER = String.raw`
import sys,json,smtplib,ssl,socket,signal,ipaddress
from email.message import EmailMessage
from email.utils import formataddr
signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(TimeoutError()))
signal.alarm(25)
config=json.load(open(sys.argv[1]));client=None;phase='connexion'
try:
 local=config.get('localName') or None
 if config.get('tls'):client=smtplib.SMTP_SSL(config['host'],int(config['port']),timeout=8,context=ssl.create_default_context(),local_hostname=local)
 else:client=smtplib.SMTP(config['host'],int(config['port']),timeout=8,local_hostname=local)
 client.ehlo_or_helo_if_needed()
 if not config.get('tls') and client.has_extn('starttls'):
  phase='TLS';client.starttls(context=ssl.create_default_context());client.ehlo()
 if config.get('username'):
  phase='authentification'
  try:loopback=ipaddress.ip_address(client.sock.getpeername()[0]).is_loopback
  except ValueError:loopback=False
  if not isinstance(client.sock,ssl.SSLSocket) and not loopback:raise ValueError('Le relais ne propose pas TLS : les identifiants n’ont pas été transmis.')
  client.user=config['username'];client.password=config.get('password','')
  method=config.get('authMethod','PLAIN')
  client.auth(method,client.auth_login if method=='LOGIN' else client.auth_plain,initial_response_ok=method!='LOGIN')
 phase='envoi'
 message=EmailMessage();message['From']=formataddr((config.get('senderName',''),config['senderAddress']));message['To']=config['to'];message['Subject']='Test SMTP - Gestion PocketBase'
 message.set_content('Ce message confirme que le relais SMTP accepte les emails de Gestion PocketBase.')
 client.send_message(message)
 result={'ok':True,'message':'Email de test accepté par le relais SMTP. La réception dans la boîte destinataire reste à vérifier.'}
except smtplib.SMTPAuthenticationError as e:result={'ok':False,'message':'Authentification SMTP refusée : vérifiez l’utilisateur, le mot de passe et la méthode PLAIN/LOGIN.','smtpCode':e.smtp_code}
except smtplib.SMTPSenderRefused as e:result={'ok':False,'message':'Adresse expéditeur refusée par le relais : utilisez une adresse autorisée pour ce compte SMTP.','smtpCode':e.smtp_code}
except smtplib.SMTPRecipientsRefused:result={'ok':False,'message':'Adresse destinataire refusée par le relais SMTP.'}
except ssl.SSLError:result={'ok':False,'message':'La connexion TLS a échoué. Vérifiez le mode TLS direct et le certificat du relais.'}
except socket.gaierror:result={'ok':False,'message':'Hôte SMTP introuvable : vérifiez son nom DNS.'}
except (TimeoutError,socket.timeout):result={'ok':False,'message':'Délai SMTP dépassé pendant '+phase+' : vérifiez le port, le mode TLS et le pare-feu.'}
except ConnectionRefusedError:result={'ok':False,'message':'Connexion SMTP refusée : vérifiez l’hôte et le port.'}
except ValueError as e:result={'ok':False,'message':str(e) if phase=='authentification' else 'Configuration SMTP invalide.'}
except Exception:result={'ok':False,'message':'Le relais SMTP a refusé le test pendant '+phase+'. Vérifiez les paramètres et les autorisations du compte.'}
finally:
 if client:
  try:client.close()
  except Exception:pass
print(json.dumps(result))
`
