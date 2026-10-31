import { describe, expect, it } from 'vitest'
import { nextWebQueuedJob } from './webQueue'

const job = (id: string, appId: string) => ({ id, appId })
const tenants: Record<string, string> = { a1: 'A', a2: 'A', b1: 'B', legacy1: '', legacy2: '' }
const pick = (queue: ReturnType<typeof job>[], served: Record<string, number> = {}) =>
  nextWebQueuedJob(
    queue,
    (appId) => tenants[appId]!,
    (appId) => served[appId] || 0
  )?.id

describe('nextWebQueuedJob', () => {
  it('file vide : rien à servir', () => expect(pick([])).toBeUndefined())
  it('un seul locataire : FIFO strict, même sur plusieurs applications', () => {
    expect(pick([job('1', 'a1'), job('2', 'a1')], { a1: 5 })).toBe('1')
    expect(pick([job('1', 'a1'), job('2', 'a2')], { a1: 5 })).toBe('1')
    expect(pick([job('1', 'legacy1'), job('2', 'legacy2')], { legacy1: 5 })).toBe('1')
  })
  it('plusieurs locataires : une application jamais servie passe devant', () => {
    expect(pick([job('1', 'a1'), job('2', 'a1'), job('3', 'b1')], { a1: 5 })).toBe('3')
  })
  it('plusieurs locataires : ancienneté du dernier job servi, puis ancienneté en file', () => {
    expect(pick([job('1', 'a1'), job('3', 'b1')], { a1: 9, b1: 5 })).toBe('3')
    expect(pick([job('1', 'a1'), job('3', 'b1')], { a1: 5, b1: 9 })).toBe('1')
    expect(pick([job('1', 'a1'), job('3', 'b1')])).toBe('1')
  })
  it('un locataire qui enfile ne passe jamais devant son propre job le plus ancien', () => {
    expect(pick([job('2', 'a1'), job('1', 'b1'), job('3', 'b1')], { a1: 9, b1: 5 })).toBe('1')
  })
  it('alterne entre deux locataires tant que les deux ont du travail', () => {
    const served: Record<string, number> = { a1: 1 },
      queue = [job('a', 'a1'), job('b', 'a1'), job('c', 'a1'), job('x', 'b1'), job('y', 'b1')],
      order: string[] = []
    for (let time = 2; queue.length; time++) {
      const id = pick(queue, served)!,
        index = queue.findIndex((row) => row.id === id)
      order.push(id)
      served[queue[index]!.appId] = time
      queue.splice(index, 1)
    }
    expect(order).toEqual(['x', 'a', 'y', 'b', 'c'])
  })
})
