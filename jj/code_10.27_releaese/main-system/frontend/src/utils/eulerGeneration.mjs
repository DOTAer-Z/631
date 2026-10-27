const allSupported = items => !!items?.length && items.every(item => item.status === 'supported')

export function generationSelection(code, target, policy, capabilities, alternatives) {
  return { executed: code, supported: allSupported(capabilities[code]), options: undefined }
}

// UI availability preview only. The backend and Agent still validate the request.
export function automaticGenerationSelection(code, capabilities, alternatives) {
  return generationSelection(code, code, 'native_only', capabilities, alternatives)
}
