import type { LayoutLoad } from './$types'

const pages: Record<string, { title: string; description: string }> = {
  '3.0': {
    title: 'PocketHost 3.0',
    description: 'Ce qui change avec PocketHost 3.0 : SFTP, fin de Flounder, tarifs et préparation.',
  },
  about: {
    title: 'À propos de PocketHost',
    description: 'Notre histoire, notre engagement open source et notre communauté.',
  },
  privacy: {
    title: 'Politique de confidentialité',
    description: 'Politique de confidentialité de PocketHost.',
  },
  terms: {
    title: "Conditions d'utilisation",
    description: "Conditions d'utilisation de PocketHost.",
  },
}

export const load: LayoutLoad = async ({ url }) => {
  const slug = url.pathname.split('/').filter(Boolean).pop() ?? ''
  const meta = pages[slug]

  return {
    url: url.href,
    meta: {
      title: meta?.title ?? 'PocketHost',
      pageTitle: meta ? `${meta.title} - PocketHost` : 'PocketHost',
      description: meta?.description ?? '',
    },
  }
}
