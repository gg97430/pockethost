/** An organizational label only; it never grants access to an instance. Empty removes the label. */
export const normalizeInstanceProjectLabel = (value: unknown): string => {
  if (typeof value !== 'string' || /[\u0000-\u001f\u007f]/.test(value))
    throw new Error('Le label doit être un texte sur une seule ligne.')
  const label = value.normalize('NFC').trim().replace(/ +/g, ' ')
  if (Array.from(label).length > 60) throw new Error('Le label est limité à 60 caractères.')
  return label
}
