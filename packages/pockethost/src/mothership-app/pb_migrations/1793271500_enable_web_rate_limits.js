/// <reference path="../src/types/types.d.ts" />
// Préparation de la limitation native par IP réelle, sans l'activer globalement : ce choix appartient à l'opérateur.
// Les limites des sites sont facultatives et configurées par projet (web_projects.limits).
// Le contrôleur n'écoute que sur 127.0.0.1 : son seul client public est le pare-feu,
// qui écrit toujours cet en-tête (FIREWALL_CLIENT_IP_HEADER, proxyHeaders.ts). À déployer APRÈS le pare-feu.
//
// Appels locaux sans en-tête (RealIP = 127.0.0.1) : worker, services web, connexions FTP et superusers des services.
// `excludedIPs` compare la RealIP, pas le pair TCP : une requête relayée reste limitée. Tant que le pare-feu n'est pas à
// jour, tout le trafic reste exclu (aucune limite, comme avant) au lieu de partager un seul seau.
//
// Les règles locales passent AVANT `/api/` : PocketBase garde l'ordre enregistré et prend le premier préfixe qui
// correspond, sans chercher le plus long. Un libellé finissant par `/` couvre le chemin nu et tout ce qui suit ; sans
// `/` final, il ne vise que le chemin exact : `/api/web-apps/_` ne couvrirait rien, chaque espace est donc listé.
const HEADER = 'X-Pockethost-Firewall-Client-Ip'
const LOCAL_ADDRESSES = ['127.0.0.1', '::1']
const LOCAL_NAMESPACES = ['_worker', '_terminal', '_health', '_tasks', '_observability', '_notifications', '_platform']
const LOCAL_LABELS = LOCAL_NAMESPACES.map((name) => `/api/web-apps/${name}/`)
const WEB_RULES = [
  { label: '*:auth', maxRequests: 2, duration: 3 },
  { label: '*:create', maxRequests: 20, duration: 5 },
  { label: '/api/batch', maxRequests: 3, duration: 1 },
  { label: '/api/', maxRequests: 300, duration: 10 },
]
const plain = ({ label, maxRequests, duration, audience }) => ({
  label,
  maxRequests,
  duration,
  audience: audience || '',
})
const currentRules = (settings) => Array.from(settings.rateLimits.rules, plain)
migrate(
  (app) => {
    const settings = app.settings()
    const kept = currentRules(settings).filter((rule) => !LOCAL_LABELS.includes(rule.label))
    const labels = new Set(kept.map((rule) => rule.label))
    settings.trustedProxy.headers = [HEADER]
    settings.trustedProxy.useLeftmostIP = false
    settings.rateLimits.excludedIPs = [...new Set([...Array.from(settings.rateLimits.excludedIPs), ...LOCAL_ADDRESSES])]
    settings.rateLimits.rules = [
      ...LOCAL_LABELS.map((label) => plain({ label, maxRequests: 1000000, duration: 1 })),
      ...kept,
      ...WEB_RULES.filter((rule) => !labels.has(rule.label)).map(plain),
    ]
    app.save(settings)
  },
  (app) => {
    const settings = app.settings()
    settings.trustedProxy.headers = []
    settings.rateLimits.excludedIPs = Array.from(settings.rateLimits.excludedIPs).filter(
      (ip) => !LOCAL_ADDRESSES.includes(ip)
    )
    settings.rateLimits.rules = currentRules(settings).filter((rule) => !LOCAL_LABELS.includes(rule.label))
    app.save(settings)
  }
)
