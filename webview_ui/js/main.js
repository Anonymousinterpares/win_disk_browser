window.addEventListener('pywebviewready', function() {
    // --- DOM ELEMENTS ---
    const driveSelect = document.getElementById('drive-select');
    const scanButton = document.getElementById('scan-button');
    const cacheButton = document.getElementById('cache-button');
    const liveMonitorToggle = document.getElementById('live-monitor-toggle');
    const chartToggle = document.getElementById('chart-toggle');
    const chartContainer = document.getElementById('chart-container');
    const statusBar = document.getElementById('status-bar');
    const searchInput = document.getElementById('search-input');
    const searchResultsContainer = document.getElementById('search-results-container');
    const resetViewButton = document.getElementById('reset-view-button');
    const breadcrumbContainer = document.getElementById('breadcrumb-container');
    const contextMenu = document.getElementById('context-menu');
    const treemapModeToggle = document.getElementById('treemap-mode-toggle');
    const structureModeBtn = document.getElementById('structure-mode-btn');
    const toplistModeBtn = document.getElementById('toplist-mode-btn');
    const treeviewModeBtn = document.getElementById('treeview-mode-btn');
    const topListControls = document.getElementById('top-list-controls');
    const topListTypeRadios = document.querySelectorAll('input[name="toplist-type"]');
    const topListSlider = document.getElementById('toplist-slider');
    const topListPrevBtn = document.getElementById('toplist-prev-btn');
    const topListNextBtn = document.getElementById('toplist-next-btn');
    const topListCounter = document.getElementById('toplist-counter');
    
    // TreeView elements
    const treeviewContainer = document.getElementById('treeview-container');
    const treeView = document.getElementById('tree-view');
    const treeSearch = document.getElementById('tree-search');
    const expandAllBtn = document.getElementById('expand-all-btn');
    const collapseAllBtn = document.getElementById('collapse-all-btn');
    const navBackBtn = document.getElementById('nav-back-btn');
    const navForwardBtn = document.getElementById('nav-forward-btn');
    const scanProgressOverlay = document.getElementById('scan-progress-overlay');
    const scanProgressTitle = document.getElementById('scan-progress-title');
    const scanProgressBar = document.getElementById('scan-progress-bar');
    const scanProgressStatus = document.getElementById('scan-progress-status');
    const scanProgressDetail = document.getElementById('scan-progress-detail');

    // --- DEBUGGING CONFIGURATION ---
    const DEBUG_TREEVIEW = false; // Set to true to enable TreeView debugging
    const treeViewDebug = {
        log: (...args) => { if (DEBUG_TREEVIEW) console.log('[TreeView DEBUG]', ...args); },
        error: (...args) => { if (DEBUG_TREEVIEW) console.error('[TreeView ERROR]', ...args); },
        time: (label) => { if (DEBUG_TREEVIEW) console.time(`[TreeView TIMING] ${label}`); },
        timeEnd: (label) => { if (DEBUG_TREEVIEW) console.timeEnd(`[TreeView TIMING] ${label}`); }
    };
    
    // TreeView operation state management
    let treeViewOperationState = {
        isNavigating: false,
        isLoading: false,
        lastClickTime: 0,
        activeTimeouts: new Map(),
        treeSelectionInProgress: false,
        pendingTreeSelections: new Set(),
        sortColumn: null,
        sortDirection: null, // 'asc', 'desc', or null
        currentContents: null // Store current directory contents for re-sorting
    };
    
    // TreeView cleanup function
    function cleanupTreeViewState() {
        treeViewDebug.log('Cleaning up TreeView state');
        
        // Cancel all active timeouts
        treeViewOperationState.activeTimeouts.forEach((data, timeoutId) => {
            treeViewDebug.log('Cancelling timeout:', data.action, 'for', data.item);
            clearTimeout(timeoutId);
        });
        treeViewOperationState.activeTimeouts.clear();
        
        // Reset state (but preserve sort preferences)
        treeViewOperationState.isNavigating = false;
        treeViewOperationState.isLoading = false;
        treeViewOperationState.lastClickTime = 0;
        treeViewOperationState.treeSelectionInProgress = false;
        treeViewOperationState.pendingTreeSelections.clear();
        // Note: We preserve sortColumn, sortDirection, and currentContents
        // to maintain user's sort preference across mode switches
        
        treeViewDebug.log('TreeView state cleaned up');
    }

    // --- STATE MANAGEMENT ---
    let chartInstance = echarts.init(chartContainer, 'dark');
    let currentView = 'treemap';
    let treemapMode = 'treeview'; // 'structure', 'toplist', or 'treeview'
    let topListState = {
        type: 'folders',
        offset: 0,
        limit: 50, // How many items to show at once
        total: 0
    };
    let currentPath = '';
    let currentZoom = 1.0;
    let navStack = [];
    let navHistory = []; // Full navigation history for forward/back buttons
    let navHistoryIndex = -1; // Current position in history
    let isHistoryNavigation = false; // Flag to prevent adding to history during history navigation
    let viewportSize = { width: 1280, height: 800 };
    let isQuickPreview = false;
    let aggregationData = new Map(); 
    let currentRootPath = '';
    let isRefreshingLive = false;
    let liveMonitorEnabled = false;
    let currentDatasetGeneration = 0;
    let fullScanInProgress = false;
    let activeOperation = null;

    function acceptDatasetPayload(data) {
        if (!data || data._datasetGeneration == null) {
            return true;
        }
        if (data._datasetGeneration !== currentDatasetGeneration) {
            console.log(
                `Ignoring stale dataset #${data._datasetGeneration} (current #${currentDatasetGeneration})`
            );
            return false;
        }
        return true;
    }

    function setControlsEnabled(enabled) {
        scanButton.disabled = !enabled;
        cacheButton.disabled = !enabled;
        driveSelect.disabled = !enabled;
    }

    function truncatePath(path, maxLen = 88) {
        if (!path) return '';
        if (path.length <= maxLen) return path;
        return '…' + path.slice(-(maxLen - 1));
    }

    function itemsToProgressPercent(itemsScanned) {
        if (!itemsScanned || itemsScanned <= 0) return null;
        return Math.min(92, 12 * Math.log10(itemsScanned + 1));
    }

    function clearViewForOperation() {
        chartInstance.clear();
        chartInstance.hideLoading();
        breadcrumbContainer.textContent = '';
        resetViewButton.style.display = 'none';
        if ($(treeView).jstree(true)) {
            $(treeView).off('select_node.jstree');
            $(treeView).jstree('destroy');
        }
        treeView.innerHTML = '';
        const fileListContainer = document.getElementById('file-list');
        if (fileListContainer) {
            fileListContainer.innerHTML = '';
        }
        chartContainer.style.display = 'block';
        treeviewContainer.style.display = 'none';
    }

    function showScanProgress(title, statusText) {
        if (!scanProgressOverlay) return;
        scanProgressTitle.textContent = title || 'Working…';
        scanProgressStatus.textContent = statusText || 'Starting…';
        scanProgressDetail.textContent = '';
        scanProgressBar.classList.add('indeterminate');
        scanProgressBar.style.width = '';
        scanProgressOverlay.hidden = false;
    }

    function updateScanProgress(payload) {
        if (!scanProgressOverlay || scanProgressOverlay.hidden) return;
        if (!acceptDatasetPayload(payload)) return;

        const operation = payload.operation || activeOperation || 'scan';
        const itemsScanned = payload.itemsScanned || 0;
        const currentPath = payload.path || '';

        if (payload.message) {
            scanProgressStatus.textContent = payload.message;
        } else if (operation === 'cache') {
            scanProgressStatus.textContent = itemsScanned > 0
                ? `Refreshing cache — ${itemsScanned.toLocaleString()} items processed`
                : 'Loading from cache…';
        } else {
            scanProgressStatus.textContent = itemsScanned > 0
                ? `Scanning — ${itemsScanned.toLocaleString()} items processed`
                : 'Scanning disk…';
        }

        if (currentPath) {
            scanProgressDetail.textContent = truncatePath(currentPath);
        }

        const percent = itemsToProgressPercent(itemsScanned);
        if (percent != null) {
            scanProgressBar.classList.remove('indeterminate');
            scanProgressBar.style.width = `${percent}%`;
        }
    }

    function hideScanProgress() {
        if (!scanProgressOverlay) return;
        scanProgressOverlay.hidden = true;
        scanProgressBar.classList.add('indeterminate');
        scanProgressBar.style.width = '';
        activeOperation = null;
    }

    function beginLongOperation(path, operation, title) {
        activeOperation = operation;
        fullScanInProgress = true;
        isQuickPreview = false;
        clearViewForOperation();
        showScanProgress(
            title || (operation === 'cache' ? 'Loading from cache' : 'Scanning disk'),
            operation === 'cache' ? `Loading cache for ${path}…` : `Scanning ${path}…`
        );
        setControlsEnabled(false);
        statusBar.textContent = operation === 'cache'
            ? `Loading cache for ${path}…`
            : `Scanning ${path}…`;
    }

    function setOperationBusy(busy) {
        fullScanInProgress = busy;
        if (!busy) {
            chartInstance.hideLoading();
            hideScanProgress();
        }
    }

    function refreshActiveView(data) {
        if (!data) return;

        const path = data.path || currentRootPath;
        currentRootPath = path;
        currentPath = path;
        updateBreadcrumb(path);
        resetViewButton.style.display = 'block';
        resetViewButton.title = 'Reset to root view (Esc key)';

        if (currentView === 'treemap') {
            if (treemapMode === 'treeview') {
                requestTreeView(path);
            } else if (treemapMode === 'toplist') {
                requestTopListView();
            } else {
                renderView(data);
            }
        } else {
            pywebview.api.get_sunburst_adaptive_view(path, 4).then(sunburstData => {
                if (sunburstData && acceptDatasetPayload(sunburstData)) {
                    renderEnhancedSunburst(sunburstData, false);
                }
            });
        }
    }

    function syncLiveMonitoring() {
        if (!currentRootPath || !pywebview.api) return;
        if (liveMonitorEnabled) {
            pywebview.api.start_live_updates(currentRootPath);
        } else {
            pywebview.api.stop_live_updates();
        }
    }
    // --- HELPER FUNCTIONS ---
    function formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    function debounce(func, delay) {
        let timer;
        return function(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => func.apply(this, args), delay);
        };
    }

    function getViewportSize() {
        return {
            width: chartContainer.offsetWidth,
            height: chartContainer.offsetHeight
        };
    }

    // --- NAVIGATION HISTORY MANAGEMENT ---
    function addToNavigationHistory(path) {
        // Don't add to history during history navigation
        if (isHistoryNavigation) {
            return;
        }
        
        // Don't add duplicate consecutive entries
        if (navHistory.length > 0 && navHistory[navHistoryIndex] === path) {
            return;
        }
        
        // If we're not at the end of history, remove everything after current position
        if (navHistoryIndex < navHistory.length - 1) {
            navHistory = navHistory.slice(0, navHistoryIndex + 1);
        }
        
        navHistory.push(path);
        navHistoryIndex = navHistory.length - 1;
        treeViewDebug.log('Added to navigation history:', path, 'Index:', navHistoryIndex);
        updateNavigationButtons();
    }

    function updateNavigationButtons() {
        if (!navBackBtn || !navForwardBtn) {
            return;
        }
        
        const canGoBack = navHistoryIndex > 0 && navHistory.length > 0;
        const canGoForward = navHistoryIndex < navHistory.length - 1 && navHistory.length > 0;
        
        navBackBtn.disabled = !canGoBack;
        navForwardBtn.disabled = !canGoForward;
        
        // Update button titles with current state
        navBackBtn.title = canGoBack ? `Go back to: ${navHistory[navHistoryIndex - 1]}` : 'No back history (Backspace)';
        navForwardBtn.title = canGoForward ? `Go forward to: ${navHistory[navHistoryIndex + 1]}` : 'No forward history';
    }

    function navigateBack() {
        // Early validation checks
        if (!navBackBtn || navBackBtn.disabled) {
            treeViewDebug.log('Back navigation prevented: button disabled or not found');
            return;
        }
        
        if (navHistoryIndex <= 0 || navHistory.length === 0) {
            treeViewDebug.log('Back navigation prevented: no history available');
            statusBar.textContent = 'No back history available';
            return;
        }
        
        if (currentView === 'treemap' && treemapMode === 'treeview') {
            const targetPath = navHistory[navHistoryIndex - 1];
            
            // Validate target path
            if (!targetPath || typeof targetPath !== 'string') {
                treeViewDebug.error('Invalid target path for back navigation:', targetPath);
                return;
            }
            
            treeViewDebug.log(`Navigating back in history to: ${targetPath} (index: ${navHistoryIndex - 1})`);
            
            // Prevent navigation if already in progress
            if (treeViewOperationState.isNavigating || treeViewOperationState.isLoading) {
                treeViewDebug.log('Navigation already in progress, ignoring back navigation');
                return;
            }
            
            // Update index after validation
            navHistoryIndex--;
            treeViewOperationState.isNavigating = true;
            isHistoryNavigation = true;
            
            // Add navigation timeout protection
            const navigationTimeout = setTimeout(() => {
                treeViewDebug.error('Navigation timeout - forcing cleanup');
                treeViewOperationState.isNavigating = false;
                isHistoryNavigation = false;
                navHistoryIndex++; // Reset index on timeout
                statusBar.textContent = 'Navigation timeout - operation cancelled';
                updateNavigationButtons();
            }, 5000); // 5 second timeout
            
            setTimeout(() => {
                try {
                    loadDirectoryContents(targetPath);
                    // Only select tree node if path is valid and different from current
                    if (targetPath !== currentDirectoryPath) {
                        selectTreeNodeByPath(targetPath);
                    }
                    updateNavigationButtons();
                } catch (error) {
                    treeViewDebug.error('Error during back navigation:', error);
                    // Reset navigation state on error
                    navHistoryIndex++;
                    statusBar.textContent = 'Navigation failed';
                } finally {
                    clearTimeout(navigationTimeout);
                    treeViewOperationState.isNavigating = false;
                    isHistoryNavigation = false;
                }
            }, 10);
        } else if (navHistoryIndex > 0) {
            // For other modes, use history navigation
            const targetPath = navHistory[navHistoryIndex - 1];
            if (targetPath && typeof targetPath === 'string') {
                navHistoryIndex--;
                navigateToPath(targetPath, true);
                updateNavigationButtons();
            }
        }
    }

    function navigateForward() {
        // Early validation checks
        if (!navForwardBtn || navForwardBtn.disabled) {
            treeViewDebug.log('Forward navigation prevented: button disabled or not found');
            return;
        }
        
        if (navHistoryIndex >= navHistory.length - 1 || navHistory.length === 0) {
            treeViewDebug.log('Forward navigation prevented: no forward history available');
            statusBar.textContent = 'No forward history available';
            return;
        }
        
        if (currentView === 'treemap' && treemapMode === 'treeview') {
            const targetPath = navHistory[navHistoryIndex + 1];
            
            // Validate target path
            if (!targetPath || typeof targetPath !== 'string') {
                treeViewDebug.error('Invalid target path for forward navigation:', targetPath);
                return;
            }
            
            treeViewDebug.log(`Navigating forward in history to: ${targetPath} (index: ${navHistoryIndex + 1})`);
            
            // Prevent navigation if already in progress
            if (treeViewOperationState.isNavigating || treeViewOperationState.isLoading) {
                treeViewDebug.log('Navigation already in progress, ignoring forward navigation');
                return;
            }
            
            // Update index after validation
            navHistoryIndex++;
            treeViewOperationState.isNavigating = true;
            isHistoryNavigation = true;
            
            // Add navigation timeout protection
            const navigationTimeout = setTimeout(() => {
                treeViewDebug.error('Navigation timeout - forcing cleanup');
                treeViewOperationState.isNavigating = false;
                isHistoryNavigation = false;
                navHistoryIndex--; // Reset index on timeout
                statusBar.textContent = 'Navigation timeout - operation cancelled';
                updateNavigationButtons();
            }, 5000); // 5 second timeout
            
            setTimeout(() => {
                try {
                    loadDirectoryContents(targetPath);
                    // Only select tree node if path is valid and different from current
                    if (targetPath !== currentDirectoryPath) {
                        selectTreeNodeByPath(targetPath);
                    }
                    updateNavigationButtons();
                } catch (error) {
                    treeViewDebug.error('Error during forward navigation:', error);
                    // Reset navigation state on error
                    navHistoryIndex--;
                    statusBar.textContent = 'Navigation failed';
                } finally {
                    clearTimeout(navigationTimeout);
                    treeViewOperationState.isNavigating = false;
                    isHistoryNavigation = false;
                }
            }, 10);
        } else if (navHistoryIndex < navHistory.length - 1) {
            // For other modes, use history navigation
            const targetPath = navHistory[navHistoryIndex + 1];
            if (targetPath && typeof targetPath === 'string') {
                navHistoryIndex++;
                navigateToPath(targetPath, true);
                updateNavigationButtons();
            }
        }
    }

    function navigateToPath(path, isHistoryNavigation = false) {
        if (currentView === 'treemap' && treemapMode === 'treeview') {
            treeViewDebug.log(`Navigating to path: ${path} (history nav: ${isHistoryNavigation})`);
            
            if (!isHistoryNavigation) {
                addToNavigationHistory(path);
            }
            
            // Use existing TreeView navigation logic
            const startTime = Date.now();
            treeViewOperationState.isNavigating = true;
            
            setTimeout(() => {
                try {
                    requestTreeViewForPath(path).then(() => {
                        currentDirectoryPath = path;
                        updateBreadcrumb(path);
                        
                        if (!isHistoryNavigation) {
                            const treeItem = findTreeItemByPath(path);
                            if (treeItem) {
                                selectTreeItem(treeItem, path);
                            }
                        }
                    }).catch(error => {
                        treeViewDebug.error('Error during path navigation:', error);
                    }).finally(() => {
                        treeViewOperationState.isNavigating = false;
                    });
                } catch (error) {
                    treeViewDebug.error('Error during path navigation:', error);
                    treeViewOperationState.isNavigating = false;
                }
            }, 50);
        }
    }

    // --- API COMMUNICATION ---
    function refreshDriveList() {
        const api = pywebview.api.get_drive_cache_status
            ? pywebview.api.get_drive_cache_status()
            : pywebview.api.get_drives().then(drives =>
                drives.map(drive => ({ drive, has_cache: false }))
            );

        return api.then(drives => {
            const previous = driveSelect.value;
            driveSelect.innerHTML = '';
            drives.forEach(({ drive, has_cache }) => {
                const option = document.createElement('option');
                option.value = drive;
                option.textContent = has_cache ? `${drive} (cached)` : drive;
                driveSelect.appendChild(option);
            });
            if (previous && [...driveSelect.options].some(o => o.value === previous)) {
                driveSelect.value = previous;
            }
            const cachedCount = drives.filter(d => d.has_cache).length;
            if (cachedCount > 0) {
                statusBar.textContent =
                    `Ready. ${cachedCount} cached drive(s) — select one and click Load from Cache or Scan.`;
            } else {
                statusBar.textContent = 'Ready. Select a drive and click Scan Fresh.';
            }
        }).catch(error => {
            console.error('Error loading drives:', error);
            statusBar.textContent = 'Error loading drives';
        });
    }
    window.refreshDriveList = refreshDriveList;
    refreshDriveList();

    window.addEventListener('resize', debounce(() => {
        viewportSize = getViewportSize();
        chartInstance.resize();
        if (pywebview.api.set_viewport) {
            pywebview.api.set_viewport(viewportSize.width, viewportSize.height);
        }
    }, 250));

    // --- API REQUEST FUNCTIONS ---
    const requestStructureView = debounce((path, zoom = 1.0) => {
        statusBar.textContent = `Loading structure for ${path}...`;
        // Pass the zoom level to the backend
        pywebview.api.get_structure_view(path, zoom).then(data => {
            if (data) {
                renderView(data);
                statusBar.textContent = `Structure Mode - Click or Scroll on folders to explore`;
            }
        }).catch(error => {
            console.error('Error getting structure view:', error);
            statusBar.textContent = 'Error loading structure view';
        });
    }, 150);


    const requestTopListView = debounce(() => {
        statusBar.textContent = `Loading largest ${topListState.type}...`;
        chartInstance.showLoading();
        pywebview.api.get_largest_consumers(topListState.type, topListState.offset, topListState.limit)
        .then(data => {
            if (data && data.items) {
                topListState.total = data.total;
                renderTopListView(data.items);
                updateTopListControls();
                statusBar.textContent = `Showing ${topListState.offset + 1}-${Math.min(topListState.offset + topListState.limit, topListState.total)} of ${topListState.total} largest ${topListState.type}.`;
            } else {
                throw new Error("Invalid data from get_largest_consumers");
            }
        }).catch(error => {
            console.error('Error getting top list view:', error);
            statusBar.textContent = `Error loading largest ${topListState.type}.`;
        }).finally(() => {
            chartInstance.hideLoading();
        });
    }, 200);

    // --- SPATIAL ZOOM IMPLEMENTATION ---
    let zoomStack = []; // Track zoom history for spatial zoom out
    
    function getBlockUnderMouse(mouseX, mouseY) {
        try {
            // Method 1: Use ECharts' getZr to find hovered element
            const zr = chartInstance.getZr();
            const hoveredElement = zr.handler.findHover(mouseX, mouseY);
            
            console.log('[Block Detection] Hovered element:', hoveredElement);
            
            if (hoveredElement && hoveredElement.target) {
                const target = hoveredElement.target;
                console.log('[Block Detection] Target object:', target);
                console.log('[Block Detection] Target properties:', Object.keys(target));
                
                // Try multiple ways to get the data
                let data = null;
                
                // Method A: Check __ecData (common ECharts pattern)
                if (target.__ecData) {
                    data = target.__ecData;
                    console.log('[Block Detection] Found data via __ecData:', data);
                }
                
                // Method A2: Check ECharts internal properties (__ec_inner_*)
                if (!data) {
                    // Check all __ec_inner_* properties
                    for (const prop of Object.keys(target)) {
                        if (prop.startsWith('__ec_inner_')) {
                            console.log(`[Block Detection] Checking ${prop}:`, target[prop]);
                            if (target[prop] && typeof target[prop] === 'object') {
                                // BREAKTHROUGH: Check if this inner property has dataIndex
                                if (typeof target[prop].dataIndex === 'number') {
                                    console.log(`[Block Detection] Found dataIndex in ${prop}: ${target[prop].dataIndex}`);
                                    const dataIndex = target[prop].dataIndex;
                                    
                                    // Method 1: Try to get data from ECharts internal model (correct way)
                                    try {
                                        const model = chartInstance.getModel();
                                        const seriesModel = model.getComponent('series', 0);
                                        if (seriesModel) {
                                            const seriesData = seriesModel.getData();
                                            console.log(`[Block Detection] Internal series data count: ${seriesData.count()}`);
                                            
                                            if (dataIndex < seriesData.count()) {
                                                const rawDataItem = seriesData.getRawDataItem(dataIndex);
                                                console.log(`[Block Detection] Raw data item at internal index ${dataIndex}:`, rawDataItem);
                                                
                                                if (rawDataItem && rawDataItem.path) {
                                                    data = rawDataItem;
                                                    console.log(`[Block Detection] SUCCESS - Found data via internal model:`, data);
                                                    break;
                                                }
                                            } else {
                                                console.log(`[Block Detection] DataIndex ${dataIndex} exceeds internal data count ${seriesData.count()}`);
                                            }
                                        }
                                    } catch (e) {
                                        console.log(`[Block Detection] Internal model access failed:`, e.message);
                                    }
                                    
                                    // Method 2: Fallback to option series data (for comparison)
                                    if (!data) {
                                        const option = chartInstance.getOption();
                                        if (option.series && option.series[0] && option.series[0].data) {
                                            const seriesData = option.series[0].data;
                                            console.log(`[Block Detection] Option series data length: ${seriesData.length}, accessing index: ${dataIndex}`);
                                            
                                            if (dataIndex < seriesData.length) {
                                                const candidateData = seriesData[dataIndex];
                                                console.log(`[Block Detection] Option data at index ${dataIndex}:`, candidateData);
                                                
                                                if (candidateData && candidateData.path) {
                                                    data = candidateData;
                                                    console.log(`[Block Detection] SUCCESS - Found data via option series:`, data);
                                                    break;
                                                }
                                            } else {
                                                console.log(`[Block Detection] DataIndex ${dataIndex} exceeds option series length ${seriesData.length}`);
                                            }
                                        }
                                    }
                                }
                                
                                // Original checks for direct data
                                if (!data && target[prop].data && target[prop].data.path) {
                                    data = target[prop].data;
                                    console.log(`[Block Detection] Found data via ${prop}:`, data);
                                    break;
                                }
                                // Check one level deeper
                                if (!data && target[prop].dataModel && target[prop].dataModel.path) {
                                    data = target[prop].dataModel;
                                    console.log(`[Block Detection] Found data via ${prop}.dataModel:`, data);
                                    break;
                                }
                            }
                        }
                    }
                }
                
                // Method B: Check dataIndex and get from series data
                if (!data && typeof target.dataIndex === 'number') {
                    console.log('[Block Detection] Target has dataIndex:', target.dataIndex);
                    const option = chartInstance.getOption();
                    if (option.series && option.series[0] && option.series[0].data) {
                        data = option.series[0].data[target.dataIndex];
                        console.log('[Block Detection] Found data via dataIndex:', data);
                    }
                }
                
                // Method C: Check if data is directly on target
                if (!data && target.data) {
                    data = target.data;
                    console.log('[Block Detection] Found data directly on target:', data);
                }
                
                // Method D: Check for __ecComponentInfo (ECharts component info)
                if (!data && target.__ecComponentInfo) {
                    data = target.__ecComponentInfo;
                    console.log('[Block Detection] Found data via __ecComponentInfo:', data);
                }
                
                // Method E: Check ECharts internal data structures
                if (!data) {
                    // Check if target has seriesIndex and dataIndex
                    if (typeof target.seriesIndex === 'number' && typeof target.dataIndex === 'number') {
                        console.log(`[Block Detection] Target has seriesIndex: ${target.seriesIndex}, dataIndex: ${target.dataIndex}`);
                        try {
                            const model = chartInstance.getModel();
                            const seriesModel = model.getComponent('series', target.seriesIndex);
                            if (seriesModel) {
                                const seriesData = seriesModel.getData();
                                const rawData = seriesData.getRawDataItem(target.dataIndex);
                                if (rawData) {
                                    data = rawData;
                                    console.log('[Block Detection] Found data via series model:', data);
                                }
                            }
                        } catch (e) {
                            console.log('[Block Detection] Series model access failed:', e.message);
                        }
                    }
                }
                
                // Method F: Check for ECharts element data
                if (!data && target.__ecElement) {
                    console.log('[Block Detection] Target has __ecElement, checking...');
                    if (target.__ecElement.data) {
                        data = target.__ecElement.data;
                        console.log('[Block Detection] Found data in __ecElement:', data);
                    }
                }
                
                // Method H: Check parent object for data
                if (!data && target.parent) {
                    console.log('[Block Detection] Checking parent object:', target.parent);
                    if (target.parent.__ecData) {
                        data = target.parent.__ecData;
                        console.log('[Block Detection] Found data via parent.__ecData:', data);
                    }
                    // Also check parent's __ec_inner properties
                    if (!data) {
                        for (const prop of Object.keys(target.parent)) {
                            if (prop.startsWith('__ec_inner_')) {
                                console.log(`[Block Detection] Checking parent.${prop}:`, target.parent[prop]);
                                if (target.parent[prop] && target.parent[prop].data && target.parent[prop].data.path) {
                                    data = target.parent[prop].data;
                                    console.log(`[Block Detection] Found data via parent.${prop}:`, data);
                                    break;
                                }
                            }
                        }
                    }
                }
                
                // Method J: Simulate ECharts click to get data
                if (!data) {
                    console.log('[Block Detection] Trying click simulation...');
                    try {
                        // Create a temporary click event to see what ECharts returns
                        const clickEvent = {
                            type: 'click',
                            zrX: mouseX,
                            zrY: mouseY,
                            offsetX: mouseX,
                            offsetY: mouseY
                        };
                        
                        // Try to get what a click would return
                        const clickParams = chartInstance.getZr().handler.findHover(mouseX, mouseY);
                        if (clickParams && clickParams.target) {
                            console.log('[Block Detection] Click simulation target:', clickParams.target);
                            
                            // Check if target has ECharts data attached differently
                            const targetKeys = Object.keys(clickParams.target);
                            console.log('[Block Detection] All target keys:', targetKeys);
                            
                            // Look for any property that might contain our data
                            for (const key of targetKeys) {
                                const value = clickParams.target[key];
                                if (value && typeof value === 'object' && value.path && value.name) {
                                    data = value;
                                    console.log(`[Block Detection] Found data via property '${key}':`, data);
                                    break;
                                }
                            }
                        }
                    } catch (e) {
                        console.log('[Block Detection] Click simulation failed:', e.message);
                    }
                }
                
                // If we found data, validate it has the required properties
                if (data && data.path) {
                    console.log('[Block Detection] SUCCESS - Found valid block data:', {
                        name: data.name,
                        path: data.path,
                        is_dir: data.is_dir
                    });
                    return data;
                } else if (data) {
                    console.log('[Block Detection] Found data but missing path property:', data);
                } else {
                    console.log('[Block Detection] No data found through any method');
                }
            }
            
            console.log('[Block Detection] No hovered element found');
            return null;
        } catch (e) {
            console.error('Error finding block under mouse:', e);
            return null;
        }
    }
    
    function spatialZoomIntoBlock(blockData) {
        console.log('[Spatial Zoom] spatialZoomIntoBlock called with:', blockData);
        
        if (!blockData || !blockData.path) {
            console.log('[Spatial Zoom] No valid path for block');
            return;
        }
        
        // Check if this is an aggregated item (virtual grouping)
        const isAggregated = blockData.aggregated === true || 
                           (blockData.path && blockData.path.includes('[') && blockData.path.includes(']'));
        
        if (isAggregated) {
            console.log(`[Spatial Zoom] Block is aggregated: ${blockData.name}`);
            console.log(`[Spatial Zoom] Expanding aggregation instead of navigating`);
            
            // Extract parent path from the aggregated path
            const parentPath = blockData.path.substring(0, blockData.path.lastIndexOf('\\['));
            
            // Expand the aggregation to show its contents
            pywebview.api.expand_aggregation(parentPath, blockData.path).then(expandedItems => {
                if (expandedItems && expandedItems.length > 0) {
                    console.log(`[Spatial Zoom] Aggregation expanded with ${expandedItems.length} items`);
                    
                    // Create a synthetic view data structure from expanded items
                    const expandedData = {
                        name: blockData.name,
                        path: blockData.path,
                        value: blockData.value,
                        children: expandedItems
                    };
                    
                    // Save current state to zoom stack
                    zoomStack.push({
                        path: currentPath,
                        timestamp: Date.now()
                    });
                    
                    // Render the expanded view
                    renderView(expandedData);
                    statusBar.textContent = `Exploring ${blockData.name} - ${expandedItems.length} items`;
                } else {
                    console.log('[Spatial Zoom] Aggregation expansion returned no items');
                    statusBar.textContent = `No items found in ${blockData.name}`;
                }
            }).catch(error => {
                console.error('[Spatial Zoom] Error expanding aggregation:', error);
                statusBar.textContent = `Error expanding ${blockData.name}`;
            });
            
            return;
        }
        
        // Check if it's a directory - in treemap data, files might not have children
        // or might be indicated by absence of is_dir property
        const isDirectory = blockData.is_dir !== false; // Default to true unless explicitly false
        
        if (!isDirectory) {
            console.log(`[Spatial Zoom] Cannot zoom into file: ${blockData.name}`);
            return;
        }
        
        console.log(`[Spatial Zoom] Block appears to be a real directory, proceeding...`);
        
        // Save current state to zoom stack for zoom out functionality
        zoomStack.push({
            path: currentPath,
            timestamp: Date.now()
        });
        
        console.log(`[Spatial Zoom] Diving into: ${blockData.path}`);
        console.log(`[Spatial Zoom] Zoom stack now has ${zoomStack.length} items`);
        
        // Request the internal structure of this specific block
        requestBlockDetailView(blockData.path);
    }
    
    function spatialZoomOut() {
        console.log(`[Spatial Zoom] spatialZoomOut called. Zoom stack length: ${zoomStack.length}`);
        console.log(`[Spatial Zoom] Current path: ${currentPath}`);
        
        if (zoomStack.length === 0) {
            console.log('[Spatial Zoom] Zoom stack is empty, trying parent directory');
            
            // Try to go to parent directory
            const isRoot = currentPath.endsWith(':\\') && currentPath.length === 3;
            console.log(`[Spatial Zoom] Is at root? ${isRoot}`);
            
            if (currentPath && !isRoot) {
                let parentPath = currentPath.substring(0, currentPath.lastIndexOf('\\'));
                if (!parentPath.includes('\\')) parentPath += '\\';
                
                console.log(`[Spatial Zoom] Going to parent: ${parentPath}`);
                requestBlockDetailView(parentPath);
            } else {
                console.log('[Spatial Zoom] Already at root level, cannot zoom out further');
            }
            return;
        }
        
        // Pop the previous state
        const previousState = zoomStack.pop();
        
        console.log(`[Spatial Zoom] Zooming out to: ${previousState.path}`);
        console.log(`[Spatial Zoom] Zoom stack now has ${zoomStack.length} items`);
        
        // Request the previous view
        requestBlockDetailView(previousState.path);
    }
    
    function requestBlockDetailView(blockPath) {
        console.log(`[Block Detail] Requesting internal structure for: ${blockPath}`);
        statusBar.textContent = `Exploring ${blockPath}...`;
        
        // Request the block's internal structure using structure view
        pywebview.api.get_adaptive_lod_view(
            blockPath, 
            1.0, // Always start with zoom level 1.0 for new blocks
            viewportSize.width, 
            viewportSize.height
        ).then(data => {
            if (data) {
                currentZoom = 1.0; // Reset zoom for new block
                renderView(data);
                statusBar.textContent = `Exploring ${blockPath} - Scroll to zoom INTO folders | Right-click or scroll up to zoom OUT`;
            }
        }).catch(error => {
            console.error('Error getting block detail view:', error);
            statusBar.textContent = 'Error loading block details';
            // Restore previous state on error
            if (zoomStack.length > 0) {
                spatialZoomOut();
            }
        });
    }

    // Legacy function for backwards compatibility (remove eventually)
    function applyManualZoom(newZoom) {
        console.log(`[Legacy] applyManualZoom called with ${newZoom} - this should not be used in spatial zoom mode`);
    }

    // --- RENDERING FUNCTIONS ---
    function renderView(data, isNavigatingBack = false) {
        if (currentView === 'treemap') {
            if (treemapMode === 'toplist') {
                // This case is handled by requestTopListView directly
                return;
            }
            renderManualZoomTreemap(data, isNavigatingBack);
        } else {
            renderEnhancedSunburst(data, isNavigatingBack);
        }
        updateBreadcrumb(data.path);
        resetViewButton.style.display = 'block';
        resetViewButton.title = 'Reset to root view (Esc key)';
    }

    function renderTopListView(items) {
        // Data is reversed for horizontal bar chart so largest is at the top
        const reversedItems = [...items].reverse();

        const option = {
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                formatter: params => {
                    const item = params[0].data;
                    return `<strong>${item.name}</strong><br>Path: ${item.path}<br>Size: ${formatSize(item.value)}`;
                }
            },
            backgroundColor: '#1a1a2e',
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: {
                type: 'value',
                boundaryGap: [0, 0.01],
                axisLabel: {
                    formatter: val => formatSize(val)
                }
            },
            yAxis: {
                type: 'category',
                data: reversedItems.map(item => item.name),
                axisLabel: {
                    interval: 0,
                    overflow: 'truncate',
                    width: 150
                }
            },
            series: [
                {
                    name: 'Size',
                    type: 'bar',
                    data: reversedItems.map(item => ({
                        value: item.value,
                        name: item.name,
                        path: item.path,
                        is_dir: item.is_dir,
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                                { offset: 0, color: '#005c97' },
                                { offset: 1, color: '#363795' }
                            ])
                        }
                    })),
                    label: {
                        show: true,
                        position: 'right',
                        formatter: params => formatSize(params.value)
                    }
                }
            ]
        };
        
        currentPath = 'top-list-view'; // Use a special path to identify this mode
        chartInstance.clear();
        chartInstance.setOption(option);
        updateBreadcrumb('Top Largest ' + (topListState.type.charAt(0).toUpperCase() + topListState.type.slice(1)));
    }    

    function renderManualZoomTreemap(data, isNavigatingBack = false) {
        if (!data || typeof data !== 'object' || !data.path) {
            console.error('Invalid data received for treemap rendering:', data);
            statusBar.textContent = 'Error: Received invalid data for treemap view.';
            try {
                chartInstance.clear();
                chartInstance.setOption({ series: [{ type: 'treemap', data: [{name: 'Error: Invalid data received', value: 1, itemStyle: {color: '#58181F'}}] }] });
            } catch(e) { console.error("Failed to render error state chart", e); }
            return;
        }
        
        if (!data.children || !Array.isArray(data.children)) {
            data.children = [];
        }
        
        currentPath = data.path;
        if (!isNavigatingBack) {
            navStack.push(JSON.parse(JSON.stringify(data)));
        }
        
        const option = {
            tooltip: {
                formatter: params => {
                    // --- NEW: Check for overlay tooltip data first ---
                    // For graphic elements, ECharts passes the whole object in `params.name`.
                    if (params.componentType === 'graphic' && params.name && params.name.tooltipData) {
                        const data = params.name.tooltipData;
                        return `<strong>Largest File: ${data.name}</strong><br>Path: ${data.path}<br>Size: ${formatSize(data.size)}`;
                    }
                    // Fallback to normal block tooltip
                    const name = params.data ? params.data.name : params.name;
                    const path = params.data ? params.data.path : currentPath;
                    const value = params.data ? params.data.value : params.value;
                    return `<strong>${name}</strong><br>Path: ${path}<br>Size: ${formatSize(value)}`;
                }
            },
            backgroundColor: '#1a1a2e',
            series: [{
                type: 'treemap',
                name: data.name, 
                value: data.value,
                data: data.children.length > 0 ? data.children : [{name: 'Empty Folder', value: 1, itemStyle: {color: '#444'}}],
                animationDurationUpdate: 300,
                roam: false,
                nodeClick: false, 
                breadcrumb: { show: false },
                isLargestFileView: data.isLargestFileView === true,
                levels: [
                    { 
                        itemStyle: { borderColor: '#333', borderWidth: 3, gapWidth: 3 }, 
                        upperLabel: { 
                            show: true, 
                            height: 30, 
                            color: '#fff', 
                            backgroundColor: 'rgba(0, 0, 0, 0.3)',
                            formatter: function (params) {
                                return `Total size of contents in '${params.name}': ${formatSize(params.value)}`;
                            }
                        } 
                    },
                    { 
                        itemStyle: { borderColor: 'rgba(255, 255, 255, 0.3)', borderWidth: 2, gapWidth: 2 }, 
                        emphasis: { itemStyle: { borderColor: '#ffff00' } } 
                    }
                ],
                label: {
                    show: true,
                    formatter: p => `${p.data.name}\n${formatSize(p.data.value)}`,
                    fontSize: 12
                },
            }]
        };
        
        try {
            // First, render the main treemap without any overlays.
            chartInstance.clear();
            chartInstance.setOption(option);

            // --- START OF THE CORRECT OVERLAY LOGIC ---
            // Now that the chart is rendered, we can get the layout of each block.
            let graphicElements = [];
            if (false) { // Heatmap mode removed
                // Access the chart's internal model to get the data with layout info.
                const seriesModel = chartInstance.getModel().getComponent('series', 0);
                const seriesData = seriesModel.getData();

                // Iterate through the original data to match it with the rendered layout.
                data.children.forEach((child, index) => {
                    if (child.largest_file) {
                        // THIS IS THE CORRECT WAY to get the layout (x, y, width, height).
                        const layout = seriesData.getItemLayout(index);

                        if (layout && layout.width > 20 && layout.height > 20) {
                            const graphicElement = {
                                type: 'rect',
                                x: layout.x + 5,
                                y: layout.y + 5,
                                shape: { width: layout.width - 10, height: layout.height - 10 },
                                style: {
                                    fill: 'rgba(0, 0, 0, 0.4)',
                                    stroke: '#ffdd00',
                                    lineWidth: 2,
                                    shadowBlur: 10,
                                    shadowColor: 'rgba(0,0,0,0.5)'
                                },
                                // Attach the data directly to the element for the tooltip to use.
                                tooltipData: child.largest_file,
                                // Give it a name so ECharts can find it for tooltips.
                                name: { tooltipData: child.largest_file }
                            };
                             graphicElements.push(graphicElement);
                        }
                    }
                });
            }

            // Finally, apply the graphic overlays to the already rendered chart.
            chartInstance.setOption({
                graphic: {
                    elements: graphicElements
                }
            });
            // --- END OF THE CORRECT OVERLAY LOGIC ---

        } catch (error) {
            console.error("ECharts rendering failed:", error);
        }
    }

    function renderEnhancedSunburst(data, isNavigatingBack = false) {
        // Safety check: ensure data exists and has required properties
        if (!data || typeof data !== 'object') {
            console.error('Invalid data provided to renderEnhancedSunburst:', data);
            return;
        }
        
        // CRITICAL: Check if the root data itself has undefined name
        if (!data.name || data.name === undefined || data.name === 'undefined') {
            console.error('ROOT DATA has undefined name:', data);
            // Try to fix the root data name
            if (data.path) {
                data.name = data.path.split('\\').pop() || data.path.split('/').pop() || 'Unknown Folder';
                console.log('Fixed root data name to:', data.name);
            } else {
                data.name = 'Unknown Folder';
            }
        }
        
        // Ensure data has children array and validate children
        if (!Array.isArray(data.children)) {
            data.children = [];
        }
        
        // Filter out any undefined or invalid children
        data.children = data.children.filter(child => {
            if (!child || typeof child !== 'object') {
                console.warn('Filtered out invalid child:', child);
                return false;
            }
            if (!child.name || child.name === 'undefined' || child.name === undefined) {
                console.warn('Filtered out child with undefined name:', child);
                return false;
            }
            // Filter out items that look like serialized JSON
            if (typeof child.name === 'string' && (child.name.includes('{"') || child.name.includes('"value":'))) {
                console.warn('Filtered out child with JSON-like name:', child.name.substring(0, 100) + '...');
                return false;
            }
            // Filter out items with malformed paths
            if (child.path && typeof child.path === 'string' && (child.path.includes('{"') || child.path.includes('"value":'))) {
                console.warn('Filtered out child with JSON-like path:', child.path.substring(0, 100) + '...');
                return false;
            }
            return true;
        });
        
        // This function recursively walks the data tree and ensures any node that is a directory
        // has a `children` array. This prevents the ECharts error permanently.
        function sanitizeSunburstData(node) {
            if (!node || typeof node !== 'object') return; // Safety check
            
            // Fix undefined names
            if (!node.name || node.name === undefined || node.name === 'undefined') {
                console.warn('Sanitizing node with undefined name:', node);
                node.name = 'Unknown Item';
            }
            
            // Fix malformed JSON-like names
            if (typeof node.name === 'string' && (node.name.includes('{"') || node.name.includes('"value":'))) {
                console.warn('Sanitizing node with JSON-like name');
                node.name = 'Corrupted Data';
            }

            // If it's a directory, we MUST process its children.
            if (node.is_dir) {
                // If children is missing or not an array, create an empty one.
                if (!Array.isArray(node.children)) {
                    node.children = [];
                }
                // Filter children before processing
                node.children = node.children.filter(child => {
                    if (!child || typeof child !== 'object') return false;
                    if (!child.name || child.name === undefined || child.name === 'undefined') return false;
                    if (typeof child.name === 'string' && (child.name.includes('{"') || child.name.includes('"value":'))) return false;
                    return true;
                });
                // Double-check that children is still an array before calling forEach
                if (Array.isArray(node.children) && node.children.length > 0) {
                    // Use a try-catch to handle any forEach errors
                    try {
                        node.children.forEach(sanitizeSunburstData);
                    } catch (error) {
                        console.error('Error in sanitizeSunburstData forEach:', error);
                        node.children = []; // Reset to empty array on error
                    }
                }
            }
        }
        sanitizeSunburstData(data);
        
        // Simple final check for undefined items
        if (data.children && Array.isArray(data.children)) {
            data.children = data.children.filter(child => {
                const isValid = child && typeof child === 'object' && child.name && child.name !== 'undefined' && child.name !== undefined;
                if (!isValid) {
                    console.warn('Removing invalid child from final data:', child);
                }
                return isValid;
            });
        }
        
        // Debug: Log final data structure being sent to sunburst
        console.log('Final sunburst data structure:', {
            name: data.name,
            path: data.path,
            childrenCount: data.children?.length,
            children: data.children?.map(child => ({
                name: child.name,
                path: child.path,
                is_dir: child.is_dir,
                hasChildren: Array.isArray(child.children) && child.children.length > 0
            }))
        });
        
        // Additional debug: Check for any undefined names in children
        if (data.children && Array.isArray(data.children)) {
            const undefinedItems = data.children.filter(child => !child || !child.name || child.name === 'undefined' || child.name === undefined);
            if (undefinedItems.length > 0) {
                console.error(`Found ${undefinedItems.length} undefined items in children:`, undefinedItems);
            }
        }

        currentPath = data.path;
        if (!isNavigatingBack) {
            navStack.push(JSON.parse(JSON.stringify(data)));
        }
        
        // Use the actual data name, or extract from path as fallback
        const folderName = data.name || data.path.split('\\').pop() || data.path.split('/').pop() || 'Root';
        
        const option = {
            backgroundColor: '#1a1a2e',
            title: {
                text: folderName,
                textStyle: { fontSize: 18, color: '#fff', fontWeight: 'normal' },
                left: 'center',
                top: 'center'
            },
            tooltip: {
                formatter: (params) => {
                    const item = params.data;
                    
                    // Block tooltips for undefined items but don't log error (to avoid flooding)
                    if (!item || !item.name || item.name === undefined || item.name === 'undefined') {
                        return ''; // Return empty string to hide tooltip
                    }
                    
                    // Show normal tooltips for valid items
                    let tooltip = `<strong>${item.name}</strong><br>`;
                    tooltip += `Path: ${item.path || 'N/A'}<br>`;
                    tooltip += `Size: ${formatSize(item.value || 0)}`;
                    if (item.hasMore || item.aggregated) {
                        tooltip += `<br><em>Click to explore</em>`;
                    }
                    return tooltip;
                }
            },
            series: [{
                type: 'sunburst',
                data: (() => {
                    if (data.children.length === 0) {
                        return [{name: 'Empty folder', value: 1, itemStyle: {color: '#444'}}];
                    }
                    
                    const filteredChildren = data.children.filter(child => 
                        child && child.name && child.name !== 'undefined' && child.name !== undefined
                    );
                    
                    console.log('Sunburst series data after final filter:', {
                        originalCount: data.children.length,
                        filteredCount: filteredChildren.length,
                        filteredItems: filteredChildren.map(child => ({
                            name: child.name,
                            hasUndefinedName: !child.name || child.name === undefined || child.name === 'undefined'
                        }))
                    });
                    
                    return filteredChildren;
                })(),
                radius: [60, '95%'],
                minAngle: 2,
                sort: 'desc',
                renderLabelForZeroData: false,
                label: {
                    rotate: 'tangential',
                    show: true,
                    overflow: 'truncate',
                    minAngle: 5,
                    formatter: function(params) {
                        if (!params || !params.data || !params.data.name) {
                            return '';
                        }
                        const name = params.data.name;
                        if (typeof name !== 'string') {
                            return String(name);
                        }
                        if (name.length > 15) return name.substring(0, 12) + '...';
                        return name;
                    }
                },
                itemStyle: { borderRadius: 7, borderWidth: 2, borderColor: '#1a1a1a' },
                emphasis: { focus: 'ancestor', itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0, 0, 0, 0.5)' } },
                levels: [{}, { r0: '20%', r: '40%', itemStyle: { borderWidth: 2 }, label: { position: 'outside', silent: false } }, { r0: '40%', r: '65%', label: { position: 'outside', padding: 3, silent: false } }, { r0: '65%', r: '85%', label: { position: 'outside', padding: 3, silent: false } }]
            }]
        };
        
        chartInstance.clear();
        chartInstance.setOption(option);
    }


    // --- EVENT HANDLERS ---
    chartInstance.on('click', function(params) {
        if (!params.data) return;
        const clickedData = params.data;

        if (currentView === 'treemap' && treemapMode === 'structure') {
            if ((clickedData.is_dir || clickedData.aggregated) && clickedData.path) {
                currentZoom = 1.0;
                requestStructureView(clickedData.path, currentZoom);
            }
        } 
        else if (currentView === 'sunburst') {
            // Allow navigation into directories and aggregated items
            if (clickedData.path && (clickedData.is_dir === true || clickedData.aggregated === true)) {
                let pathToNavigate = clickedData.path;
                
                // For aggregated items, extract the parent directory path
                if (clickedData.aggregated === true) {
                    pathToNavigate = clickedData.path.replace(/\\\[.*?\]$/, '');
                    console.log(`Navigating to aggregated item parent: ${pathToNavigate}`);
                }
                
                pywebview.api.get_sunburst_adaptive_view(pathToNavigate, 4).then(newData => {
                    if (newData && typeof newData === 'object' && newData.path) {
                        renderView(newData);
                    } else if (newData === null && clickedData.aggregated) {
                        // This is expected for aggregated chunks - they expand in place
                        console.log('Aggregated chunk expanded successfully (API returned null as expected)');
                    } else {
                        console.error('Invalid newData received from get_sunburst_adaptive_view:', newData);
                    }
                }).catch(error => {
                    console.error('Error in get_sunburst_adaptive_view:', error);
                });
            }
        }
    });



    // SPATIAL ZOOM: Mouse wheel handler for Nanite-style zoom
chartContainer.addEventListener('wheel', function(event) {
        // --- MODIFICATION: Check if we are in the special largest files view ---
        const option = chartInstance.getOption();
        const isLargestFileView = option && option.series[0] && option.series[0].isLargestFileView;

        if (false) { // Heatmap mode removed
            event.preventDefault();
            
            const rect = chartContainer.getBoundingClientRect();
            const mouseX = event.clientX - rect.left;
            const mouseY = event.clientY - rect.top;
            const delta = event.deltaY;
            
            if (delta < 0) { // Zoom in
                const hoveredBlock = getBlockUnderMouse(mouseX, mouseY);
                if (hoveredBlock && (hoveredBlock.is_dir || hoveredBlock.aggregated)) {
                    spatialZoomIntoBlock(hoveredBlock);
                }
            } else { // Zoom out
                spatialZoomOut();
            }
        } else if (currentView === 'treemap' && treemapMode === 'structure') {
            // Keep structure mode zoom-as-click functionality
            event.preventDefault();
            const delta = event.deltaY;
            if (delta < 0) {
                 const rect = chartContainer.getBoundingClientRect();
                 const mouseX = event.clientX - rect.left;
                 const mouseY = event.clientY - rect.top;
                 const hoveredBlock = getBlockUnderMouse(mouseX, mouseY);
                 if (hoveredBlock && (hoveredBlock.is_dir || hoveredBlock.aggregated)) {
                    currentZoom = 1.0;
                    requestStructureView(hoveredBlock.path, currentZoom);
                 }
            } else {
                 const isRoot = currentPath.endsWith(':\\') && currentPath.length === 3;
                 if (currentPath && !isRoot) {
                    let parentPath = currentPath.substring(0, currentPath.lastIndexOf('\\'));
                    if (!parentPath.includes('\\')) parentPath += '\\';
                    currentZoom = 1.0;
                    requestStructureView(parentPath, currentZoom);
                 }
            }
        }
    });

    // Search functionality
    const debouncedSearch = debounce((query) => {
        pywebview.api.search_nodes(query).then(displaySearchResults);
    }, 300);

    searchInput.addEventListener('input', () => {
        const query = searchInput.value.trim();
        if (query.length < 2) {
            searchResultsContainer.style.display = 'none';
            return;
        }
        debouncedSearch(query);
    });

    function displaySearchResults(results) {
        searchResultsContainer.innerHTML = '';
        
        if (results.length === 0) {
            searchResultsContainer.innerHTML = '<div class="no-results">No results found.</div>';
        } else {
            results.forEach(item => {
                const resultItem = document.createElement('div');
                resultItem.className = 'search-result-item';
                resultItem.innerHTML = `
                    <span class="name">${item.name}</span>
                    <span class="path">${item.path}</span>
                    <span class="size">${formatSize(item.value)}</span>
                `;
                resultItem.addEventListener('click', () => {
                    const parentPath = item.path.substring(0, item.path.lastIndexOf('\\'));
                    if (parentPath) {
                        setTreemapMode('structure');
                        requestStructureView(parentPath || item.path);
                    }
                    searchInput.value = '';
                    searchResultsContainer.style.display = 'none';
                });
                searchResultsContainer.appendChild(resultItem);
            });
        }
        
        searchResultsContainer.style.display = 'block';
    }

    // Scan button
    scanButton.addEventListener('click', () => {
        const selectedDrive = driveSelect.value;
        if (!selectedDrive) {
            statusBar.textContent = 'Please select a drive.';
            return;
        }
        
        currentRootPath = selectedDrive;
        navStack = [];
        aggregationData.clear();
        
        beginLongOperation(selectedDrive, 'scan', 'Scanning disk');
        
        viewportSize = getViewportSize();
        pywebview.api.set_viewport && pywebview.api.set_viewport(viewportSize.width, viewportSize.height);
        
        if (pywebview.api.stop_live_updates) {
            pywebview.api.stop_live_updates();
        }
        try {
            pywebview.api.start_scan(selectedDrive).then(generation => {
                if (generation) {
                    currentDatasetGeneration = generation;
                }
            });
        } catch (error) {
            console.error('Error starting scan:', error);
            window.onScanFailed('Failed to start scan: ' + (error.message || error));
        }
    });

    if (liveMonitorToggle) {
        liveMonitorToggle.addEventListener('change', () => {
            liveMonitorEnabled = liveMonitorToggle.checked;
            syncLiveMonitoring();
            if (liveMonitorEnabled) {
                statusBar.textContent = 'Live monitoring enabled.';
            } else {
                statusBar.textContent = 'Live monitoring disabled.';
            }
        });
    }

    // Cache button
    cacheButton.addEventListener('click', () => {
        const selectedDrive = driveSelect.value;
        if (!selectedDrive) {
            statusBar.textContent = 'Please select a drive.';
            return;
        }
        
        currentRootPath = selectedDrive;
        navStack = [];
        aggregationData.clear();
        
        beginLongOperation(selectedDrive, 'cache', 'Loading from cache');
        cacheButton.textContent = 'Load from Cache';

        viewportSize = getViewportSize();
        if (pywebview.api.set_viewport) {
            pywebview.api.set_viewport(viewportSize.width, viewportSize.height);
        }

        if (pywebview.api.stop_live_updates) {
            pywebview.api.stop_live_updates();
        }
        
        try {
            pywebview.api.load_from_cache(selectedDrive, true, false).then(generation => {
                if (generation) {
                    currentDatasetGeneration = generation;
                }
            });
        } catch (error) {
            console.error('Cache load error:', error);
            window.onScanFailed('Cache load error: ' + (error.message || error));
        }
    });

    // View toggle
chartToggle.addEventListener('change', () => {
    currentView = chartToggle.checked ? 'sunburst' : 'treemap';
    treemapModeToggle.style.display = (currentView === 'treemap') ? 'flex' : 'none';
    
    // Hide toplist controls if we are not in treemap view
    if (currentView !== 'treemap') {
        topListControls.style.display = 'none';
    }

    if (currentRootPath) { // Check if data is loaded
        navStack = [];
        if (currentView === 'sunburst') {
            pywebview.api.get_sunburst_adaptive_view(currentRootPath, 4).then(data => {
                if (data) renderEnhancedSunburst(data, false);
            });
        } else {
            // Default to structure mode when switching back to treemap view
            setTreemapMode('structure'); 
        }
    }
});

    function setTreemapMode(mode) {
        const previousMode = treemapMode;
        treemapMode = mode;
        
        // Cleanup TreeView state when switching away from TreeView
        if (previousMode === 'treeview' && mode !== 'treeview') {
            cleanupTreeViewState();
        }
        
        structureModeBtn.classList.toggle('active', mode === 'structure');
        toplistModeBtn.classList.toggle('active', mode === 'toplist');
        treeviewModeBtn.classList.toggle('active', mode === 'treeview');

        topListControls.style.display = (mode === 'toplist') ? 'flex' : 'none';
        
        // Show/hide containers based on mode
        if (mode === 'treeview') {
            chartContainer.style.display = 'none';
            treeviewContainer.style.display = 'flex';
            treeViewDebug.log('Switched to TreeView mode');
            
            // Reset navigation history when switching to TreeView
            if (previousMode !== 'treeview') {
                navHistory = [];
                navHistoryIndex = -1;
                treeViewDebug.log('Navigation history reset for TreeView mode');
            }
            
            // Always update navigation buttons when entering TreeView mode
            setTimeout(() => {
                updateNavigationButtons();
            }, 100);
        } else {
            chartContainer.style.display = 'block';
            treeviewContainer.style.display = 'none';
            // Force chart resize after UI changes to ensure proper height calculation
            setTimeout(() => {
                chartInstance.resize();
            }, 0);
        }
        
        // When switching modes, fetch the appropriate data
        if (currentRootPath) {
            if (treemapMode === 'structure') {
                requestStructureView(currentRootPath);
            } else if (treemapMode === 'treeview') {
                requestTreeView(currentRootPath);
            } else if (treemapMode === 'toplist') {
                // Reset state and fetch for top list
                topListState.offset = 0;
                requestTopListView();
            }
        }
    }

    // --- WINDOWS EXPLORER STYLE TREEVIEW (lazy-loaded) ---
    let currentDirectoryPath = '';

    function getParentPath(path) {
        if (!path) return null;
        if (path.length === 3 && path[1] === ':' && path[2] === '\\') return null;
        const lastSlash = path.lastIndexOf('\\');
        if (lastSlash <= 0) return null;
        let parent = path.substring(0, lastSlash);
        if (parent.length === 2 && parent[1] === ':') parent += '\\';
        return parent;
    }

    function fetchTreeChildren(parentPath) {
        return pywebview.api.get_directory_tree_children(parentPath).then(response => {
            if (response.error) {
                throw new Error(response.error);
            }
            return response.nodes || [];
        });
    }

    function requestTreeView(path) {
        statusBar.textContent = `Loading Windows Explorer view for ${path}...`;

        const loadRoot = pywebview.api.get_directory_tree_root
            ? pywebview.api.get_directory_tree_root(path)
            : pywebview.api.get_directory_tree(path).then(response => ({
                success: response.success,
                node: response.data,
                stats: {
                    root_path: response.stats.root_path,
                    child_count: Math.max(0, (response.stats.directories || 1) - 1),
                },
                error: response.error,
            }));

        loadRoot.then(response => {
            if (response.error) {
                statusBar.textContent = `TreeView Error: ${response.error}`;
                return;
            }

            currentRootPath = response.stats.root_path;
            renderDirectoryTreeLazy(response.node);

            const childCount = response.stats.child_count ?? 0;
            statusBar.textContent = `Folder tree ready (${childCount} top-level folders)`;

            currentDirectoryPath = currentRootPath;

            if (navHistory.length === 0) {
                navHistory = [currentRootPath];
                navHistoryIndex = 0;
                treeViewDebug.log('Initialized navigation history with root:', currentRootPath);
            }

            loadDirectoryContents(currentDirectoryPath);

            setTimeout(() => {
                updateNavigationButtons();
            }, 200);
        }).catch(error => {
            console.error('Error loading TreeView:', error);
            statusBar.textContent = `TreeView Error: ${error.message}`;
        });
    }

    function renderDirectoryTreeLazy(rootNode) {
        if ($(treeView).jstree(true)) {
            $(treeView).off('select_node.jstree');
            $(treeView).jstree('destroy');
        }

        $(treeView).jstree({
            core: {
                data: function(node, callback) {
                    if (node.id === '#') {
                        callback([rootNode]);
                        return;
                    }
                    const nodePath = node.data && node.data.path;
                    if (!nodePath) {
                        callback([]);
                        return;
                    }
                    fetchTreeChildren(nodePath)
                        .then(children => callback(children))
                        .catch(err => {
                            console.error('Failed to load tree children:', err);
                            callback([]);
                        });
                },
                check_callback: true,
                themes: {
                    name: 'default',
                    dots: true,
                    icons: true
                }
            },
            types: {
                drive: { icon: 'jstree-folder' },
                folder: { icon: 'jstree-folder' }
            },
            plugins: ['types', 'search']
        });

        $(treeView).on('select_node.jstree', function(e, data) {
            const nodeData = data.node.data;
            if (nodeData && nodeData.path) {
                currentDirectoryPath = nodeData.path;
                loadDirectoryContents(nodeData.path);
                statusBar.textContent = `Directory: ${nodeData.path}`;
            }
        });
    }

    function expandPathInTree(targetPath) {
        return new Promise((resolve) => {
            const tree = $(treeView).jstree(true);
            if (!tree || !targetPath || !currentRootPath) {
                resolve();
                return;
            }

            const pathChain = [];
            let cursor = targetPath;
            while (cursor && cursor !== currentRootPath) {
                pathChain.unshift(cursor);
                cursor = getParentPath(cursor);
            }

            if (pathChain.length === 0) {
                resolve();
                return;
            }

            let index = 0;
            const openNext = () => {
                if (index >= pathChain.length) {
                    resolve();
                    return;
                }
                const nodePath = pathChain[index];
                if (tree.get_node(nodePath)) {
                    tree.open_node(nodePath, () => {
                        index += 1;
                        setTimeout(openNext, 0);
                    });
                } else {
                    const parentPath = getParentPath(nodePath) || currentRootPath;
                    tree.open_node(parentPath, () => {
                        setTimeout(() => {
                            index += 1;
                            openNext();
                        }, 50);
                    });
                }
            };
            openNext();
        });
    }
    
    function loadDirectoryContents(path) {
        treeViewDebug.log('loadDirectoryContents called with path:', path);
        treeViewDebug.time('loadDirectoryContents');
        
        // Prevent multiple simultaneous loads
        if (treeViewOperationState.isLoading) {
            treeViewDebug.log('Already loading, ignoring request for:', path);
            return;
        }
        
        treeViewOperationState.isLoading = true;
        
        try {
            // Hide any open context menu when navigating
            contextMenu.style.display = 'none';
            
            // Add visual loading indicator
            const fileListContainer = document.getElementById('file-list');
            if (fileListContainer) {
                fileListContainer.style.opacity = '0.5';
                fileListContainer.style.pointerEvents = 'none';
            }
            
            // Update current directory path
            const previousPath = currentDirectoryPath;
            currentDirectoryPath = path;
            treeViewDebug.log('Updated currentDirectoryPath to:', currentDirectoryPath);
            
            // Add to navigation history if this is a user-initiated navigation
            if (previousPath && previousPath !== path && !isHistoryNavigation) {
                addToNavigationHistory(path);
            }
            
            // Update breadcrumb
            updateBreadcrumb(path);
            
            // Show loading state
            statusBar.textContent = `Loading directory: ${path}...`;
            
            // Load directory contents
            pywebview.api.get_directory_contents(path).then(response => {
                treeViewDebug.time('API Response Processing');
                
                if (response.error) {
                    treeViewDebug.error('API returned error:', response.error);
                    statusBar.textContent = `Error: ${response.error}`;
                    return;
                }
                
                treeViewDebug.log('API returned', response.contents?.length || 0, 'items');
                
                // Update current path after successful load
                currentPath = path;
                
                renderDirectoryContents(response.contents);
                statusBar.textContent = `${path} - ${response.stats.folders} folders, ${response.stats.files} files`;
                
                treeViewDebug.timeEnd('API Response Processing');
                
            }).catch(error => {
                treeViewDebug.error('API call failed:', error);
                statusBar.textContent = `Error loading directory: ${error.message || error}`;
            }).finally(() => {
                // Restore visual state
                const fileListContainer = document.getElementById('file-list');
                if (fileListContainer) {
                    fileListContainer.style.opacity = '1';
                    fileListContainer.style.pointerEvents = 'auto';
                }
                
                treeViewOperationState.isLoading = false;
                treeViewDebug.timeEnd('loadDirectoryContents');
            });
            
        } catch (error) {
            treeViewDebug.error('Synchronous error in loadDirectoryContents:', error);
            
            // Restore visual state
            const fileListContainer = document.getElementById('file-list');
            if (fileListContainer) {
                fileListContainer.style.opacity = '1';
                fileListContainer.style.pointerEvents = 'auto';
            }
            
            treeViewOperationState.isLoading = false;
            statusBar.textContent = `Error: ${error.message}`;
        }
    }
    
    function renderDirectoryContents(contents) {
        treeViewDebug.log('renderDirectoryContents called with', contents?.length || 0, 'items');
        treeViewDebug.time('renderDirectoryContents');
        
        // Store contents for re-sorting
        treeViewOperationState.currentContents = contents;
        
        const fileListContainer = document.getElementById('file-list');
        if (!fileListContainer) {
            treeViewDebug.error('file-list container not found');
            return;
        }
        
        try {
            // Clear existing contents and any attached event listeners
            treeViewDebug.time('DOM Cleanup');
            fileListContainer.innerHTML = '';
            treeViewDebug.timeEnd('DOM Cleanup');
            
            if (contents.length === 0) {
                fileListContainer.innerHTML = '<div class="empty-folder">This folder is empty</div>';
                treeViewDebug.timeEnd('renderDirectoryContents');
                return;
            }
            
            // Apply current sort if any
            const sortedContents = applySortToContents(contents);
            
            // Create table
            treeViewDebug.time('Table Creation');
            const table = document.createElement('table');
            table.className = 'file-list-table';
            
            // Create sortable header
            const thead = document.createElement('thead');
            thead.innerHTML = `
                <tr>
                    <th class="name-column sortable-header" data-sort="name">
                        Name <span class="sort-arrow"></span>
                    </th>
                    <th class="size-column sortable-header" data-sort="size">
                        Size <span class="sort-arrow"></span>
                    </th>
                    <th class="type-column sortable-header" data-sort="type">
                        Type <span class="sort-arrow"></span>
                    </th>
                    <th class="items-column sortable-header" data-sort="items">
                        Items <span class="sort-arrow"></span>
                    </th>
                </tr>
            `;
            table.appendChild(thead);
            
            // Add click handlers to sortable headers
            thead.querySelectorAll('.sortable-header').forEach(header => {
                header.addEventListener('click', () => {
                    const sortColumn = header.dataset.sort;
                    handleColumnSort(sortColumn);
                });
            });
            
            // Create body
            const tbody = document.createElement('tbody');
            treeViewDebug.timeEnd('Table Creation');
            
            treeViewDebug.time('Row Creation');
            sortedContents.forEach((item, index) => {
            const row = document.createElement('tr');
            row.className = item.is_dir ? 'folder-row' : 'file-row';
            row.dataset.path = item.path;
            row.dataset.isDir = item.is_dir;
            
            row.innerHTML = `
                <td class="name-cell">
                    <span class="file-icon">${item.is_dir ? '📁' : '📄'}</span>
                    <span class="file-name">${item.name}</span>
                </td>
                <td class="size-cell">${item.size_formatted}</td>
                <td class="type-cell">${item.type}</td>
                <td class="items-cell">${item.items_text}</td>
            `;
            
            // Generate unique row ID for debugging
            const rowId = `row-${index}-${item.name}`;
            row.setAttribute('data-row-id', rowId);
            
            // Add comprehensive event handlers with debugging
            addTreeViewEventHandlers(row, item, rowId);
            
                tbody.appendChild(row);
            });
            treeViewDebug.timeEnd('Row Creation');
            
            treeViewDebug.time('DOM Append');
            table.appendChild(tbody);
            fileListContainer.appendChild(table);
            treeViewDebug.timeEnd('DOM Append');
            
            treeViewDebug.log('Successfully rendered', sortedContents.length, 'items');
            
            // Update sort indicators after rendering
            updateSortIndicators();
            
            treeViewDebug.timeEnd('renderDirectoryContents');
            
        } catch (error) {
            treeViewDebug.error('Error in renderDirectoryContents:', error);
            treeViewOperationState.isLoading = false;
            statusBar.textContent = `Error rendering directory contents: ${error.message}`;
        }
    }
    
    // Handle column header clicks for sorting
    function handleColumnSort(column) {
        treeViewDebug.log('Column sort requested:', column);
        
        // Determine new sort direction
        if (treeViewOperationState.sortColumn === column) {
            // Same column - toggle direction: asc -> desc -> none -> asc
            if (treeViewOperationState.sortDirection === 'asc') {
                treeViewOperationState.sortDirection = 'desc';
            } else if (treeViewOperationState.sortDirection === 'desc') {
                treeViewOperationState.sortDirection = null;
                treeViewOperationState.sortColumn = null;
            } else {
                treeViewOperationState.sortDirection = 'asc';
            }
        } else {
            // New column - start with ascending
            treeViewOperationState.sortColumn = column;
            treeViewOperationState.sortDirection = 'asc';
        }
        
        treeViewDebug.log('Sort state:', treeViewOperationState.sortColumn, treeViewOperationState.sortDirection);
        
        // Re-render with new sort
        if (treeViewOperationState.currentContents) {
            renderDirectoryContents(treeViewOperationState.currentContents);
        }
    }
    
    // Apply sorting to contents array
    function applySortToContents(contents) {
        if (!treeViewOperationState.sortColumn || !treeViewOperationState.sortDirection) {
            return [...contents]; // Return copy of original order
        }
        
        const sortedContents = [...contents];
        const column = treeViewOperationState.sortColumn;
        const direction = treeViewOperationState.sortDirection;
        
        sortedContents.sort((a, b) => {
            let compareResult = 0;
            
            switch (column) {
                case 'name':
                    // Folders first, then alphabetical (case-insensitive)
                    if (a.is_dir !== b.is_dir) {
                        compareResult = a.is_dir ? -1 : 1;
                    } else {
                        compareResult = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
                    }
                    break;
                    
                case 'size':
                    // Numerical sort by size
                    compareResult = (a.size || 0) - (b.size || 0);
                    break;
                    
                case 'type':
                    // Folders first, then by type alphabetically
                    if (a.is_dir !== b.is_dir) {
                        compareResult = a.is_dir ? -1 : 1;
                    } else {
                        compareResult = (a.type || '').localeCompare(b.type || '');
                    }
                    break;
                    
                case 'items':
                    // Sort by total count for folders, 0 for files
                    const aCount = a.is_dir ? (a.file_count || 0) + (a.dir_count || 0) : 0;
                    const bCount = b.is_dir ? (b.file_count || 0) + (b.dir_count || 0) : 0;
                    compareResult = aCount - bCount;
                    break;
                    
                default:
                    compareResult = 0;
            }
            
            // Apply sort direction
            return direction === 'desc' ? -compareResult : compareResult;
        });
        
        return sortedContents;
    }
    
    // Update visual sort indicators
    function updateSortIndicators() {
        const headers = document.querySelectorAll('.sortable-header');
        
        headers.forEach(header => {
            const arrow = header.querySelector('.sort-arrow');
            const column = header.dataset.sort;
            
            // Remove existing classes
            header.classList.remove('sort-active', 'sort-asc', 'sort-desc');
            
            if (column === treeViewOperationState.sortColumn && treeViewOperationState.sortDirection) {
                header.classList.add('sort-active', `sort-${treeViewOperationState.sortDirection}`);
                arrow.textContent = treeViewOperationState.sortDirection === 'asc' ? '▲' : '▼';
            } else {
                arrow.textContent = '';
            }
        });
    }
    
    // Separate function to handle event listeners with proper debugging and state management
    function addTreeViewEventHandlers(row, item, rowId) {
        treeViewDebug.log('Adding event handlers for row:', rowId, 'item:', item.name);
        
        // Single click handler to prevent conflicts with double-click
        let singleClickTimeout;
        row.addEventListener('click', (e) => {
            const clickTime = Date.now();
            treeViewDebug.log('Single click on', rowId, 'at', clickTime);
            
            e.preventDefault();
            e.stopPropagation();
            
            // Clear any existing single-click timeout
            if (singleClickTimeout) {
                clearTimeout(singleClickTimeout);
            }
            
            // Debounce single clicks
            singleClickTimeout = setTimeout(() => {
                treeViewDebug.log('Processing single click for:', rowId);
                // Single click behavior - just select the row for now
                document.querySelectorAll('.file-list-table tbody tr').forEach(tr => tr.classList.remove('selected'));
                row.classList.add('selected');
            }, 200); // Wait for potential double-click
        });
        
        // Double-click handler with comprehensive debugging
        row.addEventListener('dblclick', (e) => {
            const clickTime = Date.now();
            treeViewDebug.log('Double click on', rowId, 'at', clickTime);
            treeViewDebug.time(`Double-click-${rowId}`);
            
            e.preventDefault();
            e.stopPropagation();
            
            // Clear single-click timeout since this is a double-click
            if (singleClickTimeout) {
                clearTimeout(singleClickTimeout);
                singleClickTimeout = null;
            }
            
            // Prevent multiple rapid double-clicks
            if (treeViewOperationState.isNavigating) {
                treeViewDebug.log('Navigation already in progress, ignoring double-click');
                return;
            }
            
            if (clickTime - treeViewOperationState.lastClickTime < 300) {
                treeViewDebug.log('Too rapid double-click, ignoring');
                return;
            }
            
            treeViewOperationState.lastClickTime = clickTime;
            treeViewOperationState.isNavigating = true;
            
            try {
                if (item.is_dir) {
                    treeViewDebug.log('Double-click navigation to folder:', item.path);
                    
                    // Use setTimeout to break out of event handling context
                    const timeoutId = setTimeout(() => {
                        try {
                            loadDirectoryContents(item.path);
                            
                            // Only attempt tree selection if rapid clicking isn't happening
                            if (Date.now() - treeViewOperationState.lastClickTime > 500) {
                                selectTreeNodeByPath(item.path);
                            } else {
                                treeViewDebug.log('Skipping tree selection due to rapid clicking');
                            }
                        } catch (error) {
                            treeViewDebug.error('Error during navigation:', error);
                        } finally {
                            treeViewOperationState.isNavigating = false;
                            treeViewOperationState.activeTimeouts.delete(timeoutId);
                            treeViewDebug.timeEnd(`Double-click-${rowId}`);
                        }
                    }, 10);
                    
                    treeViewOperationState.activeTimeouts.set(timeoutId, { item: item.name, action: 'navigate' });
                    
                } else {
                    treeViewDebug.log('Double-click on file, opening location:', item.path);
                    const parentPath = item.path.substring(0, item.path.lastIndexOf('\\'));
                    
                    pywebview.api.open_location(parentPath).then(success => {
                        treeViewDebug.log('Open location result:', success);
                        statusBar.textContent = success ? `Opened: ${parentPath}` : `Failed to open: ${parentPath}`;
                    }).catch(error => {
                        treeViewDebug.error('Error opening location:', error);
                    }).finally(() => {
                        treeViewOperationState.isNavigating = false;
                        treeViewDebug.timeEnd(`Double-click-${rowId}`);
                    });
                }
            } catch (error) {
                treeViewDebug.error('Synchronous error in double-click handler:', error);
                treeViewOperationState.isNavigating = false;
                treeViewDebug.timeEnd(`Double-click-${rowId}`);
            }
        });
        
        // Right-click context menu with debugging
        row.addEventListener('contextmenu', (e) => {
            treeViewDebug.log('Context menu on', rowId);
            e.preventDefault();
            e.stopPropagation();
            
            try {
                showTreeViewContextMenu(e, item);
            } catch (error) {
                treeViewDebug.error('Error showing context menu:', error);
            }
        });
        
        // Track event handler addition
        treeViewDebug.log('Event handlers added successfully for:', rowId);
    }
    
    function selectTreeNodeByPath(path) {
        if (treeViewOperationState.treeSelectionInProgress) {
            treeViewDebug.log('Tree selection already in progress, queuing:', path);
            treeViewOperationState.pendingTreeSelections.add(path);
            return;
        }

        if (treeViewOperationState.pendingTreeSelections.size > 3) {
            treeViewDebug.log('Too many pending tree selections, skipping:', path);
            return;
        }

        treeViewDebug.log('selectTreeNodeByPath called with path:', path);
        treeViewOperationState.treeSelectionInProgress = true;

        const tree = $(treeView).jstree(true);
        if (!tree) {
            treeViewDebug.error('jsTree not initialized');
            treeViewOperationState.treeSelectionInProgress = false;
            return;
        }

        expandPathInTree(path).then(() => {
            try {
                tree.deselect_all();
                if (tree.get_node(path)) {
                    tree.select_node(path);
                    const nodeElement = tree.get_node(path, true);
                    if (nodeElement && nodeElement.length > 0) {
                        nodeElement[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }
            } catch (error) {
                treeViewDebug.error('Error selecting tree node:', error);
            } finally {
                treeViewOperationState.treeSelectionInProgress = false;
                processNextTreeSelection();
            }
        }).catch(error => {
            treeViewDebug.error('Error expanding tree path:', error);
            treeViewOperationState.treeSelectionInProgress = false;
            processNextTreeSelection();
        });
    }
    
    // Process the next pending tree selection
    function processNextTreeSelection() {
        if (treeViewOperationState.pendingTreeSelections.size > 0) {
            const nextPath = treeViewOperationState.pendingTreeSelections.values().next().value;
            treeViewOperationState.pendingTreeSelections.delete(nextPath);
            
            // Clear other pending selections to avoid backlog
            treeViewOperationState.pendingTreeSelections.clear();
            
            treeViewDebug.log('Processing next pending tree selection:', nextPath);
            setTimeout(() => selectTreeNodeByPath(nextPath), 100);
        }
    }
    
    function showFileContextMenu(e, item) {
        // Position and show context menu
        contextMenu.style.left = e.pageX + 'px';
        contextMenu.style.top = e.pageY + 'px';
        contextMenu.style.display = 'block';
        
        // Build context menu
        contextMenu.innerHTML = '';
        
        // Open location
        const openItem = document.createElement('div');
        openItem.className = 'context-menu-item';
        openItem.innerHTML = item.is_dir ? '📂 Open Folder' : '🗂️ Open File Location';
        openItem.onclick = () => {
            contextMenu.style.display = 'none';
            const pathToOpen = item.is_dir ? item.path : item.path.substring(0, item.path.lastIndexOf('\\'));
            pywebview.api.open_location(pathToOpen);
        };
        contextMenu.appendChild(openItem);
        
        // Show in other modes
        const showInStructureItem = document.createElement('div');
        showInStructureItem.className = 'context-menu-item';
        showInStructureItem.innerHTML = '📊 Show in Structure Mode';
        showInStructureItem.onclick = () => {
            contextMenu.style.display = 'none';
            const pathToShow = item.is_dir ? item.path : item.path.substring(0, item.path.lastIndexOf('\\'));
            setTreemapMode('structure');
            pywebview.api.get_view(pathToShow).then(data => {
                renderView(data);
            });
        };
        contextMenu.appendChild(showInStructureItem);
        
    }

    // TreeView-specific context menu function
    function showTreeViewContextMenu(e, item) {
        // Position and show context menu
        contextMenu.style.left = e.pageX + 'px';
        contextMenu.style.top = e.pageY + 'px';
        contextMenu.style.display = 'block';
        
        // Build context menu
        contextMenu.innerHTML = '';
        
        // Open location item
        const openItem = document.createElement('div');
        openItem.className = 'context-menu-item';
        if (item.is_dir) {
            openItem.innerHTML = '📂 Open Folder';
        } else {
            openItem.innerHTML = '🗂️ Open File Location';
        }
        openItem.onclick = () => {
            contextMenu.style.display = 'none';
            const pathToOpen = item.is_dir ? item.path : item.path.substring(0, item.path.lastIndexOf('\\'));
            pywebview.api.open_location(pathToOpen).then(success => {
                if (success) {
                    statusBar.textContent = `Opened: ${pathToOpen}`;
                } else {
                    statusBar.textContent = `Failed to open: ${pathToOpen}`;
                }
            }).catch(error => {
                console.error('Error opening location:', error);
                statusBar.textContent = `Error opening: ${pathToOpen}`;
            });
        };
        contextMenu.appendChild(openItem);
        
        // Add separator
        const separator1 = document.createElement('div');
        separator1.className = 'context-menu-separator';
        contextMenu.appendChild(separator1);
        
        // Show in Structure Mode
        const showInStructureItem = document.createElement('div');
        showInStructureItem.className = 'context-menu-item';
        showInStructureItem.innerHTML = '📊 Show in Structure Mode';
        showInStructureItem.onclick = () => {
            contextMenu.style.display = 'none';
            const pathToShow = item.is_dir ? item.path : item.path.substring(0, item.path.lastIndexOf('\\'));
            setTreemapMode('structure');
            requestStructureView(pathToShow);
        };
        contextMenu.appendChild(showInStructureItem);
        
        
        // Show in Sunburst Mode (for folders)
        if (item.is_dir) {
            const showInSunburstItem = document.createElement('div');
            showInSunburstItem.className = 'context-menu-item';
            showInSunburstItem.innerHTML = '☀️ Show in Sunburst Mode';
            showInSunburstItem.onclick = () => {
                contextMenu.style.display = 'none';
                currentView = 'sunburst';
                chartToggle.checked = true;
                pywebview.api.get_sunburst_adaptive_view(item.path, 4).then(data => {
                    if (data) renderView(data);
                });
            };
            contextMenu.appendChild(showInSunburstItem);
        }
    }
    
    function updateBreadcrumb(path) {
        // Simple breadcrumb implementation
        const parts = path.split('\\').filter(p => p);
        const breadcrumb = document.getElementById('tree-breadcrumb');
        if (breadcrumb) {
            breadcrumb.innerHTML = parts.map((part, index) => {
                const fullPath = parts.slice(0, index + 1).join('\\') + (index === 0 ? '\\' : '');
                return `<span class="breadcrumb-item" data-path="${fullPath}">${part}</span>`;
            }).join(' > ');
            
            // Add click handlers for breadcrumb navigation
            breadcrumb.querySelectorAll('.breadcrumb-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    const targetPath = item.dataset.path;
                    console.log(`TreeView: Breadcrumb navigation to: ${targetPath}`);
                    loadDirectoryContents(targetPath);
                    selectTreeNodeByPath(targetPath);
                });
            });
        }
    }
    
    // TreeView controls
    if (expandAllBtn) {
        expandAllBtn.addEventListener('click', () => {
            $(treeView).jstree('open_all');
            statusBar.textContent = 'Expanded all directories';
        });
    }
    
    if (collapseAllBtn) {
        collapseAllBtn.addEventListener('click', () => {
            $(treeView).jstree('close_all');
            statusBar.textContent = 'Collapsed all directories';
        });
    }
    
    // Navigation buttons with error handling
    if (navBackBtn) {
        navBackBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            try {
                navigateBack();
            } catch (error) {
                treeViewDebug.error('Error in back navigation click handler:', error);
                statusBar.textContent = 'Navigation error - please try again';
            }
        });
    }
    
    if (navForwardBtn) {
        navForwardBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            try {
                navigateForward();
            } catch (error) {
                treeViewDebug.error('Error in forward navigation click handler:', error);
                statusBar.textContent = 'Navigation error - please try again';
            }
        });
    }
    
    // Directory search
    if (treeSearch) {
        let searchTimeout;
        treeSearch.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                const query = e.target.value;
                if (query.trim()) {
                    $(treeView).jstree('search', query);
                } else {
                    $(treeView).jstree('clear_search');
                }
            }, 300);
        });
    }

    structureModeBtn.addEventListener('click', () => setTreemapMode('structure'));
    toplistModeBtn.addEventListener('click', () => setTreemapMode('toplist'));
    treeviewModeBtn.addEventListener('click', () => setTreemapMode('treeview'));

    // Top List controls
    topListTypeRadios.forEach(radio => {
        radio.addEventListener('change', () => {
            topListState.type = document.querySelector('input[name="toplist-type"]:checked').value;
            topListState.offset = 0; // Reset to beginning
            requestTopListView();
        });
    });

    topListSlider.addEventListener('input', () => {
        topListState.offset = parseInt(topListSlider.value, 10);
        updateTopListCounter();
    });

    topListSlider.addEventListener('change', () => {
        // Fetch data only when user releases the slider
        requestTopListView();
    });

    topListPrevBtn.addEventListener('click', () => {
        if (topListState.offset > 0) {
            topListState.offset = Math.max(0, topListState.offset - topListState.limit);
            requestTopListView();
        }
    });

    topListNextBtn.addEventListener('click', () => {
        if (topListState.offset + topListState.limit < topListState.total) {
            topListState.offset += topListState.limit;
            requestTopListView();
        }
    });

    // Reset view button
    resetViewButton.addEventListener('click', () => {
        if (currentRootPath) {
            navStack = [];
            currentZoom = 1.0;
            if (currentView === 'sunburst') {
                pywebview.api.get_sunburst_adaptive_view(currentRootPath, 4).then(data => {
                    if (data) renderEnhancedSunburst(data, false);
                });
            } else {
                setTreemapMode('structure');
                requestStructureView(currentRootPath);
            }
        }
    });

    function updateTopListControls() {
        const maxOffset = Math.max(0, topListState.total - topListState.limit);
        topListSlider.max = maxOffset;
        topListSlider.value = topListState.offset;
        updateTopListCounter();
    }

    function updateTopListCounter() {
        const start = topListState.total > 0 ? topListState.offset + 1 : 0;
        const end = Math.min(topListState.offset + topListState.limit, topListState.total);
        topListCounter.textContent = `${start} - ${end} of ${topListState.total}`;
    }

    // Breadcrumb navigation
    function updateBreadcrumb(path) {
        breadcrumbContainer.innerHTML = '';
        if (!path) return;
        
        const parts = path.replace(/\\/g, '/').split('/').filter(p => p);
        let pathSegments = [];

        parts.forEach((part, index) => {
            pathSegments.push(part);
            
            let currentBuiltPath = pathSegments.join('\\');
            if (index === 0 && currentBuiltPath.endsWith(':')) {
                currentBuiltPath += '\\';
            }
            
            if (index > 0) {
                const separator = document.createElement('span');
                separator.className = 'breadcrumb-separator';
                separator.textContent = ' › ';
                breadcrumbContainer.appendChild(separator);
            }

            const link = document.createElement('a');
            link.className = 'breadcrumb-link';
            link.textContent = part;
            link.setAttribute('data-path', currentBuiltPath);
            
            link.addEventListener('click', (event) => {
                const targetPath = event.currentTarget.getAttribute('data-path');
                
                navStack = []; 
                if (currentView === 'sunburst') {
                    pywebview.api.get_sunburst_adaptive_view(targetPath, 4).then(data => {
                        if(data) renderView(data);
                    });
                } else {
                    setTreemapMode('structure');
                    requestStructureView(targetPath);
                }
            });
            
            breadcrumbContainer.appendChild(link);
        });
    }
    
    function showContextMenu(x, y, hoveredData = null) {
        let itemData = hoveredData;
        if (!itemData) {
            const rect = chartContainer.getBoundingClientRect();
            const chartX = x - rect.left;
            const chartY = y - rect.top;
            itemData = getBlockUnderMouse(chartX, chartY);
        }
        
        // ECharts events pass data differently, check for that format
        if (hoveredData && hoveredData.data && hoveredData.data.path) {
            itemData = hoveredData.data;
        }

        if (!itemData || !itemData.path) {
            console.log('No valid item data for context menu', itemData);
            return;
        }
        
        // Debug context menu data
        const isAggregatedItem = itemData.path && (itemData.path.includes('[') || itemData.path.includes(']'));
        console.log('Context menu itemData:', {
            name: itemData.name,
            path: itemData.path,
            is_dir: itemData.is_dir,
            aggregated: itemData.aggregated,
            isAggregatedForContext: isAggregatedItem
        });
    
        contextMenu.innerHTML = '';
        contextMenu.style.left = `${x}px`;
        contextMenu.style.top = `${y}px`;
        contextMenu.style.display = 'block';

        // --- NEW LOGIC FOR TOP LIST MODE ---
        if (treemapMode === 'toplist') {
            const openItem = document.createElement('div');
            openItem.className = 'context-menu-item';
            
            let path_to_open = itemData.path;
            if (itemData.is_dir) {
                openItem.innerHTML = '🗂️ Open Folder';
            } else {
                openItem.innerHTML = '📂 Open Containing Folder';
                // For files, we need to open the parent directory
                path_to_open = itemData.path.substring(0, itemData.path.lastIndexOf('\\'));
            }

            openItem.onclick = () => {
                contextMenu.style.display = 'none';
                pywebview.api.open_location(path_to_open).then(success => {
                    statusBar.textContent = success ? `Opened: ${path_to_open}` : `Failed to open: ${path_to_open}`;
                }).catch(error => {
                    console.error('Error opening location:', error);
                    statusBar.textContent = `Error opening: ${path_to_open}`;
                });
            };
            contextMenu.appendChild(openItem);
            return; // Don't show other options for this mode
        }

        const isRealPath = itemData.path && !itemData.path.includes('[') && !itemData.path.includes(']');
        
        if (isRealPath || isAggregatedItem) {
            const openLocationItem = document.createElement('div');
            openLocationItem.className = 'context-menu-item';
            openLocationItem.innerHTML = isAggregatedItem ? '📂 Open Parent Folder' : '🗂️ Open Location';
            openLocationItem.onclick = () => {
                contextMenu.style.display = 'none';
                
                let pathToOpen = itemData.path;
                
                // For aggregated items, extract the parent path
                if (isAggregatedItem) {
                    // Remove the bracketed part: \[more_files] or \[more_folders]
                    pathToOpen = itemData.path.replace(/\\\[.*?\]$/, '');
                    
                    // Additional safety check - ensure it's a valid Windows path
                    if (!pathToOpen || pathToOpen.length < 3) {
                        console.error('Invalid path after extraction:', pathToOpen, 'from:', itemData.path);
                        statusBar.textContent = 'Error: Could not determine folder path';
                        return;
                    }
                    
                    console.log(`Aggregated item path extraction:`);
                    console.log(`  Original: ${itemData.path}`);
                    console.log(`  Extracted: ${pathToOpen}`);
                }
                
                pywebview.api.open_location(pathToOpen).then(success => {
                    if (success) {
                        statusBar.textContent = `Opened location: ${pathToOpen}`;
                    } else {
                        statusBar.textContent = `Failed to open location: ${pathToOpen}`;
                    }
                }).catch(error => {
                    console.error('Error opening location:', error);
                    statusBar.textContent = `Error opening location: ${pathToOpen}`;
                });
            };
            contextMenu.appendChild(openLocationItem);
            
            // Add separator if there will be more menu items
            if (currentView === 'treemap' && treemapMode === 'structure') {
                const separator = document.createElement('div');
                separator.className = 'context-menu-separator';
                contextMenu.appendChild(separator);
            }
            // Also add separator for sunburst if there are additional options
            if (currentView === 'sunburst' && itemData.is_dir) {
                const separator = document.createElement('div');
                separator.className = 'context-menu-separator';
                contextMenu.appendChild(separator);
            }
        }

        if (false) { // Heatmap mode removed
            const showInStructureItem = document.createElement('div');
            showInStructureItem.className = 'context-menu-item';
            showInStructureItem.innerHTML = '📂 Show in Structure Mode';
            showInStructureItem.onclick = () => {
                contextMenu.style.display = 'none';
                const parentPath = itemData.path.substring(0, itemData.path.lastIndexOf('\\'));
                if (parentPath) {
                    setTreemapMode('structure');
                    requestStructureView(parentPath || itemData.path);
                }
            };
            contextMenu.appendChild(showInStructureItem);
        }

        if (false) { // Heatmap mode removed
            const zoomOutItem = document.createElement('div');
            zoomOutItem.className = 'context-menu-item';
            zoomOutItem.innerHTML = '🔙 Spatial Zoom Out';
            zoomOutItem.onclick = () => {
                spatialZoomOut();
                contextMenu.style.display = 'none';
            };
            contextMenu.appendChild(zoomOutItem);
            
            const exploreItem = document.createElement('div');
            exploreItem.className = 'context-menu-item';
            exploreItem.innerHTML = '🔍 Explore This Folder';
            exploreItem.onclick = () => {
                if (itemData.is_dir) {
                    spatialZoomIntoBlock(itemData);
                }
                contextMenu.style.display = 'none';
            };
            contextMenu.appendChild(exploreItem);
        }
        
        // Add sunburst-specific options
        if (currentView === 'sunburst' && isRealPath && itemData.is_dir) {
            const exploreInSunburstItem = document.createElement('div');
            exploreInSunburstItem.className = 'context-menu-item';
            exploreInSunburstItem.innerHTML = '☀️ Explore in Sunburst';
            exploreInSunburstItem.onclick = () => {
                contextMenu.style.display = 'none';
                if (itemData.path && (itemData.hasMore || itemData.aggregated || itemData.is_dir)) {
                    pywebview.api.get_sunburst_adaptive_view(itemData.path, 4).then(newData => {
                        if (newData) {
                            renderView(newData);
                        }
                    }).catch(error => {
                        console.error('Error loading sunburst view:', error);
                        statusBar.textContent = 'Error loading sunburst view';
                    });
                }
            };
            contextMenu.appendChild(exploreInSunburstItem);
        }
    }

    chartInstance.on('contextmenu', function (params) {
        if (params.event && params.event.preventDefault) {
            params.event.preventDefault();
        }
        let itemData = params.data; // ECharts provides the data object directly!
        console.log('ECharts contextmenu event on item:', itemData);

        if (itemData && itemData.path) {
            showContextMenu(params.event.event.clientX, params.event.event.clientY, params);
        } else if (false) { // Heatmap mode removed
            // Fallback for heatmap to find block under mouse if ECharts event fails
            const rect = chartContainer.getBoundingClientRect();
            const chartX = params.event.event.clientX - rect.left;
            const chartY = params.event.event.clientY - rect.top;
            itemData = getBlockUnderMouse(chartX, chartY);
            if(itemData) {
                showContextMenu(params.event.event.clientX, params.event.event.clientY, {data: itemData});
            }
        }
    });

    chartContainer.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        
        // Get the item under the mouse cursor
        const rect = chartContainer.getBoundingClientRect();
        const chartX = e.clientX - rect.left;
        const chartY = e.clientY - rect.top;
        
        console.log('Right-click detected at chart coordinates:', chartX, chartY);
        
        // Try to get the item data under the mouse
        let itemData = null;
        
        if (currentView === 'treemap') {
            itemData = getBlockUnderMouse(chartX, chartY);
        } else if (currentView === 'sunburst') {
            // For sunburst, we need to use ECharts' built-in methods
            try {
                const hoveredElement = chartInstance.getZr().handler.findHover(chartX, chartY);
                if (hoveredElement && hoveredElement.target) {
                    // Try to extract data from sunburst element
                    const target = hoveredElement.target;
                    
                    // Check various ECharts internal properties for sunburst data
                    if (target.__ecData) {
                        itemData = target.__ecData;
                    } else {
                        // Look for data in internal properties
                        for (const prop of Object.keys(target)) {
                            if (prop.startsWith('__ec_inner_')) {
                                if (target[prop] && typeof target[prop].dataIndex === 'number') {
                                    const dataIndex = target[prop].dataIndex;
                                    const option = chartInstance.getOption();
                                    if (option.series && option.series[0] && option.series[0].data) {
                                        const candidateData = option.series[0].data[dataIndex];
                                        if (candidateData && candidateData.path) {
                                            itemData = candidateData;
                                            break;
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (error) {
                console.log('Error getting sunburst item data:', error);
            }
        }
        
        console.log('Item data found for context menu:', itemData);
        
        // Show context menu with the found item data
        showContextMenu(e.clientX, e.clientY, itemData);
    });

    // Hide context menu and search results
    document.addEventListener('click', (e) => {
        if (!contextMenu.contains(e.target)) {
            contextMenu.style.display = 'none';
        }
        if (!searchResultsContainer.contains(e.target) && e.target !== searchInput) {
            searchResultsContainer.style.display = 'none';
        }
    });

    // --- GLOBAL CALLBACK FUNCTIONS ---

    window.onDataChanged_v2 = async function(changedParentDirs) {
        if (isRefreshingLive) return;
        isRefreshingLive = true;

        console.log("Live update signal received for parents:", changedParentDirs);
        statusBar.textContent = "Live update received. Applying changes...";
        
        const currentActiveMode = currentView === 'sunburst' ? 'sunburst' : treemapMode;
        
        if (currentActiveMode === 'toplist') {
            // Perform a seamless update for the Top List view
            console.log("Applying seamless update to Top List view.");
            try {
                const data = await pywebview.api.get_largest_consumers(topListState.type, topListState.offset, topListState.limit);
                if (data && data.items) {
                    topListState.total = data.total;
                    
                    // Update the chart with new data without a full reload
                    const reversedItems = [...data.items].reverse();
                    chartInstance.setOption({
                        yAxis: {
                            data: reversedItems.map(item => item.name)
                        },
                        series: [{
                            data: reversedItems.map(item => ({
                                value: item.value,
                                name: item.name,
                                path: item.path,
                                is_dir: item.is_dir,
                                // Gradient must be redefined here for ECharts update
                                itemStyle: {
                                    color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                                        { offset: 0, color: '#005c97' },
                                        { offset: 1, color: '#363795' }
                                    ])
                                }
                            }))
                        }]
                    });

                    updateTopListControls(); // Update slider max and counter
                    statusBar.textContent = "Top List view updated live.";
                }
            } catch (error) {
                console.error("Error during seamless Top List update:", error);
                statusBar.textContent = "Error updating Top List.";
            }
        } else {
            // Existing logic for Treemap/Sunburst patch updates
            const option = chartInstance.getOption();
            let dataNeedsUpdate = false;

            function findAndReplaceNode(nodes, path, updatedNodeData) {
                for (let i = 0; i < nodes.length; i++) {
                    if (nodes[i].path === path) {
                        nodes[i] = updatedNodeData; dataNeedsUpdate = true; return true;
                    }
                    if (nodes[i].children && findAndReplaceNode(nodes[i].children, path, updatedNodeData)) {
                        return true;
                    }
                }
                return false;
            }

            for (const path of changedParentDirs) {
                if (currentPath.startsWith(path) || path.startsWith(currentPath)) {
                    const updatedNodeData = await pywebview.api.get_live_update_payload(path, currentActiveMode, currentZoom);
                    if (updatedNodeData && option.series[0].data) {
                        findAndReplaceNode(option.series[0].data, path, updatedNodeData);
                    }
                }
            }

            if (dataNeedsUpdate) {
                chartInstance.setOption({ series: [{ data: option.series[0].data }] });
                statusBar.textContent = "View updated live.";
            } else {
                statusBar.textContent = "Live Mode Enabled. Monitoring for changes...";
            }
        }

        setTimeout(() => { isRefreshingLive = false; }, 500);
    };

        
    window.onScanProgress = function(payload) {
        if (!payload) return;
        if (payload._datasetGeneration != null) {
            currentDatasetGeneration = payload._datasetGeneration;
        }
        if (!acceptDatasetPayload(payload)) return;

        if (payload.started && scanProgressOverlay && scanProgressOverlay.hidden) {
            const operation = payload.operation || 'cache';
            const path = payload.path || payload._datasetPath || '';
            beginLongOperation(
                path,
                operation,
                operation === 'scan' ? 'Scanning disk' : 'Loading from cache'
            );
        }
        updateScanProgress(payload);
    };

    window.onQuickPreview = function(data) {
        // Quick preview disabled — incomplete data is misleading on long scans.
        if (!acceptDatasetPayload(data)) return;
        console.log('Quick preview suppressed; waiting for full scan.');
    };

    window.onScanComplete = function(data) {
        console.log('Scan/Cache load complete:', data);
        if (!acceptDatasetPayload(data)) return;

        navStack = [];
        currentZoom = 1.0;
        fullScanInProgress = false;
        
        hideScanProgress();
        chartInstance.hideLoading();
        setControlsEnabled(true);
        
        if (data) {
            refreshActiveView(data);
            statusBar.textContent = 'Cache loaded.';

            if (liveMonitorEnabled) {
                syncLiveMonitoring();
            }

        } else {
            onCacheMiss();
        }
        isQuickPreview = false;
    };

    window.onScanFinallyComplete = function(generation) {
        console.log("Backend signaled full scan is complete. Now pulling final data...");
        if (typeof generation === 'number' && generation !== currentDatasetGeneration) {
            console.log(`Ignoring stale onScanFinallyComplete for dataset #${generation}`);
            return;
        }

        if (scanProgressBar) {
            scanProgressBar.classList.remove('indeterminate');
            scanProgressBar.style.width = '100%';
        }
        scanProgressStatus.textContent = 'Finalizing view…';
        statusBar.textContent = 'Scan complete. Preparing view…';
        
        pywebview.api.get_final_scan_data(currentDatasetGeneration).then(data => {
            console.log('Final scan data received:', data);
            if (!acceptDatasetPayload(data)) return;

            navStack = [];
            currentZoom = 1.0;
            fullScanInProgress = false;
            
            hideScanProgress();
            chartInstance.hideLoading();
            setControlsEnabled(true);
            
            if (data) {
                refreshActiveView(data);
                statusBar.textContent = 'Scan complete.';

                if (liveMonitorEnabled) {
                    syncLiveMonitoring();
                }

            } else {
                statusBar.textContent = 'Scan completed but failed to retrieve final data.';
            }
            isQuickPreview = false;
        }).catch(error => {
            console.error("Failed to pull final scan data:", error);
            onScanFailed("Error retrieving final scan results.");
        });
    };

    window.onCacheMiss = function(generation) {
        if (typeof generation === 'number' && generation !== currentDatasetGeneration) {
            return;
        }
        statusBar.textContent = 'No cache found. Please perform a fresh scan.';
        fullScanInProgress = false;
        hideScanProgress();
        setControlsEnabled(true);
        chartInstance.hideLoading();
    };

    window.onScanFailed = function(message = 'Scan failed. Please check permissions.') {
        if (message && typeof message === 'object') {
            if (message._datasetGeneration != null && message._datasetGeneration !== currentDatasetGeneration) {
                return;
            }
            message = message.message || 'Scan failed. Please check permissions.';
        }
        console.error('Scan failed:', message);
        statusBar.textContent = message;
        fullScanInProgress = false;
        hideScanProgress();
        setControlsEnabled(true);
        chartInstance.hideLoading();
    };

    // SPATIAL ZOOM: Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.target.matches('input, textarea')) return;

        // Spatial zoom shortcuts removed (heatmap mode removed)
        if (false) { // Heatmap mode removed
            if (e.key === '-' || e.key === '_') {
                e.preventDefault();
                console.log(`[Spatial Zoom] Keyboard zoom out`);
                spatialZoomOut();
            }
        }

        if (e.key === 'Backspace') {
            e.preventDefault();
            if (currentView === 'sunburst' || (currentView === 'treemap' && treemapMode === 'structure')) {
                 const isRoot = currentPath.endsWith(':\\') && currentPath.length === 3;
                 if (currentPath && !isRoot) {
                    let parentPath = currentPath.substring(0, currentPath.lastIndexOf('\\'));
                    if (!parentPath.includes('\\')) parentPath += '\\';
                    
                    if (currentView === 'sunburst') {
                        pywebview.api.get_sunburst_adaptive_view(parentPath, 4).then(data => {
                           if (data) renderEnhancedSunburst(data, true);
                        });
                    } else {
                        requestStructureView(parentPath);
                    }
                 }
            } else if (currentView === 'treemap' && treemapMode === 'treeview') {
                // Handle backspace navigation for TreeView mode with same protection as double-click
                const backspaceTime = Date.now();
                treeViewDebug.log('Backspace key pressed in TreeView mode at', backspaceTime);
                treeViewDebug.time('Backspace-Navigation');
                
                const isRoot = currentDirectoryPath && currentDirectoryPath.endsWith(':\\') && currentDirectoryPath.length === 3;
                if (currentDirectoryPath && !isRoot) {
                    let parentPath = currentDirectoryPath.substring(0, currentDirectoryPath.lastIndexOf('\\'));
                    if (!parentPath.includes('\\')) parentPath += '\\';
                    
                    treeViewDebug.log(`Navigating back from ${currentDirectoryPath} to ${parentPath}`);
                    
                    // Prevent navigation if already in progress
                    if (treeViewOperationState.isNavigating || treeViewOperationState.isLoading) {
                        treeViewDebug.log('Navigation already in progress, ignoring backspace');
                        return;
                    }
                    
                    // Prevent rapid backspace navigation (same protection as double-click)
                    if (backspaceTime - treeViewOperationState.lastClickTime < 300) {
                        treeViewDebug.log('Too rapid backspace navigation, ignoring');
                        return;
                    }
                    
                    treeViewOperationState.lastClickTime = backspaceTime;
                    treeViewOperationState.isNavigating = true;
                    
                    // Use async navigation like double-click to prevent UI blocking
                    const timeoutId = setTimeout(() => {
                        try {
                            treeViewDebug.log('Starting async backspace navigation');
                            
                            // Load parent directory contents
                            loadDirectoryContents(parentPath);
                            
                            // Only attempt tree selection if not in rapid navigation mode
                            if (Date.now() - treeViewOperationState.lastClickTime > 500) {
                                selectTreeNodeByPath(parentPath);
                            } else {
                                treeViewDebug.log('Skipping tree selection due to rapid backspace navigation');
                            }
                        } catch (error) {
                            treeViewDebug.error('Error during backspace navigation:', error);
                        } finally {
                            treeViewOperationState.isNavigating = false;
                            treeViewOperationState.activeTimeouts.delete(timeoutId);
                            treeViewDebug.timeEnd('Backspace-Navigation');
                        }
                    }, 10);
                    
                    treeViewOperationState.activeTimeouts.set(timeoutId, { item: 'parent-directory', action: 'backspace-navigate' });
                    
                } else {
                    treeViewDebug.log('Already at root directory');
                    statusBar.textContent = 'Already at root directory';
                }
            }
        }

        if (e.key === 'ArrowLeft' && e.altKey) {
            e.preventDefault();
            if (navStack.length > 1) {
                navStack.pop();
                const previousData = navStack[navStack.length - 1];
                if (previousData) {
                    if (currentView === 'sunburst') {
                        renderEnhancedSunburst(previousData, true);
                    } else {
                        renderManualZoomTreemap(previousData, true);
                    }
                    updateBreadcrumb(previousData.path);
                }
            }
        }

        if (e.key === 'Escape') {
            e.preventDefault();
            resetViewButton.click();
        }

        if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey) {
            e.preventDefault();
            chartToggle.checked = !chartToggle.checked;
            chartToggle.dispatchEvent(new Event('change'));
        }
    });

    // Initialize the application
    viewportSize = getViewportSize();
    statusBar.textContent = 'Ready. Select a drive and click Scan or Load Cache.';
    
    // Initialize TreeView mode as default
    setTreemapMode('treeview');
    
    console.log('[Init] Visual analyzer ready. TreeView mode is now the default.');
});