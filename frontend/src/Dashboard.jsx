import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import './Dashboard.css'

function Dashboard() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('Loading...')
  const [file, setFile] = useState(null)
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadError, setUploadError] = useState(false)

  // State for Processing
  const [progress, setProgress] = useState(0)
  const [isProcessing, setIsProcessing] = useState(false)
  const [processedVideo, setProcessedVideo] = useState(null)

  const [videos, setVideos] = useState([])

  const fetchVideos = async (token) => {
    try {
      const response = await axios.get('http://127.0.0.1:8000/videos', {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      setVideos(response.data.videos)
    } catch (err) {
      console.error("Could not fetch videos", err)
    }
  }

  const handleDeleteVideo = async (videoId, filename) => {
    const confirmed = window.confirm(`Delete "${filename}"? This can't be undone.`)
    if (!confirmed) return

    try {
      const token = localStorage.getItem('token')
      await axios.delete(`http://127.0.0.1:8000/videos/${videoId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      })
      setVideos((prev) => prev.filter((v) => v.id !== videoId))
    } catch (err) {
      console.error("Could not delete video", err)
    }
  }

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) {
      navigate('/login')
    } else {
      setStatus('System ready — awaiting video input')
      fetchVideos(token)
    }
  }, [navigate])

  const handleLogout = () => {
    localStorage.removeItem('token')
    navigate('/login')
  }

  const handleFileChange = (e) => {
    setFile(e.target.files[0])
    setUploadMessage('')
    setUploadError(false)
    setProcessedVideo(null) // Reset video on new file selection
  }

  const handleUpload = async () => {
    if (!file) {
      setUploadMessage('Select a video file first.')
      setUploadError(true)
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
      setUploadMessage(`Processing started for ${cleanFilename}`)
      setUploadError(false)
      setFile(null)
      fetchVideos(token)

      // WebSocket Logic
      if (response.data.task_id) {
        setIsProcessing(true)
        setProgress(0)
        setStatus('Running lane + object detection')

        const ws = new WebSocket(`ws://127.0.0.1:8000/ws/task/${response.data.task_id}`)

        ws.onmessage = (event) => {
          const wsData = JSON.parse(event.data)
          if (wsData.progress) setProgress(wsData.progress)

          if (wsData.status === "completed") {
            ws.close()

            const baseName = cleanFilename.substring(0, cleanFilename.lastIndexOf('.'))

            // Add a timestamp query parameter to bust the browser cache
            const timestamp = new Date().getTime()
            setProcessedVideo(`http://127.0.0.1:8000/video/processed_${baseName}.mp4?t=${timestamp}`)

            setIsProcessing(false)
            setStatus('System ready — awaiting video input')
          }
        }
      }
    } catch (err) {
      setUploadMessage('Upload failed. Use a valid .mp4 or .avi file.')
      setUploadError(true)
    }
  }

  return (
    <div className="dash">
      <div className="dash-shell">
        <header className="dash-topbar">
          <div className="dash-brand">
            <span className="dash-brand-mark">◈</span>
            PERCEPTION<span className="dash-brand-thin">.SYS</span>
          </div>
          <div className="dash-topbar-right">
            <div className="dash-status">
              <span className={`dash-status-dot ${isProcessing ? 'busy' : ''}`} />
              {status}
            </div>
            <button className="dash-logout" onClick={handleLogout}>Sign out</button>
          </div>
        </header>

        <section className="dash-panel">
          <div className="dash-panel-label">01 — Upload feed</div>

          <label className="dash-dropzone">
            <input type="file" accept="video/*" onChange={handleFileChange} hidden />
            <div className="dash-dropzone-inner">
              {file ? (
                <span className="dash-file-chip">{file.name}</span>
              ) : (
                <>
                  <span className="dash-dropzone-icon">⤒</span>
                  <span>Drop a clip or click to browse</span>
                  <span className="dash-dropzone-hint">MP4 or AVI</span>
                </>
              )}
            </div>
          </label>

          <button className="dash-primary-btn" onClick={handleUpload} disabled={isProcessing}>
            {isProcessing ? 'Processing…' : 'Run detection'}
          </button>

          {uploadMessage && (
            <p className={`dash-message ${uploadError ? 'error' : ''}`}>{uploadMessage}</p>
          )}
        </section>

        {isProcessing && (
          <section className="dash-panel">
            <div className="dash-panel-label">02 — Pipeline</div>
            <div className="dash-pipeline-row">
              <span className="dash-pipeline-tag lane">LANE_DETECT</span>
              <span className="dash-pipeline-tag obj">OBJ_DETECT</span>
              <span className="dash-pipeline-percent">{progress}%</span>
            </div>
            <div className="dash-scan-track">
              <div className="dash-scan-fill" style={{ width: `${progress}%` }} />
            </div>
          </section>
        )}

        {processedVideo && (
          <section className="dash-panel">
            <div className="dash-panel-label">03 — Output</div>
            <div className="dash-video-frame">
              <span className="dash-corner tl" />
              <span className="dash-corner tr" />
              <span className="dash-corner bl" />
              <span className="dash-corner br" />
              {/* key forces a fresh mount so the video element never shows stale/cached frames */}
              <video key={processedVideo} controls src={processedVideo} />
            </div>
          </section>
        )}

        <section className="dash-panel">
          <div className="dash-panel-label">Run history</div>
          {videos.length === 0 ? (
            <p className="dash-empty">No runs yet — upload a clip above to get started.</p>
          ) : (
            <ul className="dash-history-list">
              {videos.map((v) => (
                <li key={v.id}>
                  <span className="dash-history-name">{v.filename}</span>
                  <span className="dash-history-meta">
                    <span className="dash-history-date">
                      {new Date(v.upload_date).toLocaleDateString()}
                    </span>
                    <button
                      className="dash-history-delete"
                      onClick={() => handleDeleteVideo(v.id, v.filename)}
                      aria-label={`Delete ${v.filename}`}
                    >
                      ×
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}

export default Dashboard