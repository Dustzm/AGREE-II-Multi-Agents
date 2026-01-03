import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Chat } from './pages/Chat'
import { Reports } from './pages/Reports'

import { Skills } from './pages/Skills'
import { Agents } from './pages/Agents'
import { Batch } from './pages/Batch'

function App() {
  return (
    <BrowserRouter basename="/ama">
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<Chat />} />
          <Route path="reports" element={<Reports />} />
          <Route path="skills" element={<Skills />} />
          <Route path="agents" element={<Agents />} />
          <Route path="batch" element={<Batch />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
