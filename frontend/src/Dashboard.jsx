import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'

function Dashboard() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('Loading...')
  const [file, setFile] = useState(null)
  const [uploadMessage, setUploadMessage] = useState('')
  
  // State for Processing
  const [progress, setProgress] = useState(0)
  const [isProcessing, setIsProcessing] = useState(false)
  const [processedVideo, setProcessedVideo] = useState(null)
  
  const [documents, setDocuments] = useState([])

  const fetchDocuments = async (token) => {
    try {
      const response = await axios.get('http://127.0.0.1:8000/documents', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      setDocuments(response.data.documents)
    } catch (err) {
      console.error("Could not fetch documents", err)
    }
  }

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) {
      navigate('/login')
    } else {
      setStatus('System Ready. Awaiting Video Input.')
      fetchDocuments(token)
    }
  }, [navigate])

  const handleLogout = () => {
    localStorage.removeItem('token')
    navigate('/login')
  }

  const handleFileChange = (e) => {
    setFile(e.target.files[0])
    setUploadMessage('')
    setProcessedVideo(null) // Reset video on new file selection
  }

const handleUpload = async () => {
    if (!file) {
      setUploadMessage('Please select a video file first.')
      return
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
      const token = localStorage.getItem('token')
      const response = await axios.post('http://127.0.0.1:8000/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
          'Authorization': `Bearer ${token}`
        }
      })
      
      const cleanFilename = response.data.filename
      setUploadMessage(`Processing started for: ${cleanFilename}`)
      setFile(null)
      fetchDocuments(token)
      
      // WebSocket Logic
      if (response.data.task_id) {
        setIsProcessing(true)
        setProgress(0)

        const ws = new WebSocket(`ws://127.0.0.1:8000/ws/task/${response.data.task_id}`)

        ws.onmessage = (event) => {
          const wsData = JSON.parse(event.data)
          if (wsData.progress) setProgress(wsData.progress)
          
          if (wsData.status === "completed") {
            ws.close()
            
            const baseName = cleanFilename.substring(0, cleanFilename.lastIndexOf('.'))
            
            // NEW: Add a timestamp query parameter to bust the browser cache
            const timestamp = new Date().getTime()
            setProcessedVideo(`http://127.0.0.1:8000/video/processed_${baseName}.mp4?t=${timestamp}`)
            
            setIsProcessing(false)
          }
        }
      }
    } catch (err) {
      setUploadMessage('Upload failed. Ensure it is a valid .mp4 or .avi file.')
    }
  }
  return (
    <div style={{ padding: '20px', maxWidth: '600px', margin: '0 auto' }}>
      <h2>Autonomous Vehicle Perception Dashboard</h2>
      
      {/* Upload Section */}
      <div style={{ padding: '20px', border: '1px solid #ccc', borderRadius: '5px' }}>
        <input type="file" accept="video/*" onChange={handleFileChange} />
        <button onClick={handleUpload} style={{ marginLeft: '10px' }}>Process Video</button>
        
        {/* Progress Bar */}
        {isProcessing && (
          <div style={{ marginTop: '20px' }}>
            <p>Telemetry Pipeline: {progress}%</p>
            <div style={{ width: '100%', background: '#eee', height: '10px' }}>
              <div style={{ width: `${progress}%`, height: '100%', background: '#4caf50' }} />
            </div>
          </div>
        )}

        {/* Video Output */}
        {processedVideo && (
          <div style={{ marginTop: '20px' }}>
            <h3>Processed Output</h3>
            {/* Adding the key forces React to mount a brand new player, destroying the cached bug */}
            <video key={processedVideo} width="100%" controls src={processedVideo} />
          </div>
        )}
      </div>
    </div>
  )
}

export default Dashboard