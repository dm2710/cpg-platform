import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Runs an async fetcher whenever deps change.
 * Returns { data, loading, error, refetch }.
 * refetch() returns a Promise that resolves when the fetch completes,
 * so callers can await it to ensure state is up-to-date before proceeding.
 */
export function useAsync(fetcher, deps = [], options = {}) {
  const { enabled = true } = options
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState(null)
  const requestId = useRef(0)

  const run = useCallback(() => {
    if (!enabled) return Promise.resolve(null)
    const id = ++requestId.current
    setLoading(true)
    setError(null)
    return fetcher()
      .then((result) => {
        if (id === requestId.current) {
          setData(result)
          setLoading(false)
        }
        return result
      })
      .catch((err) => {
        if (id === requestId.current) {
          setError(err)
          setLoading(false)
        }
        throw err
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    run()
  }, [run])

  return { data, loading, error, refetch: run }
}
