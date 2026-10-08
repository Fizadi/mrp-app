// signature_modal.js - reusable digital signature modal
function showSignatureModal(options) {
    // options = { title, onConfirm, recordId, recordType }
    if ($('#signatureModal').length === 0) {
        $('body').append(`
            <div class="modal fade" id="signatureModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Digital Signature Required</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <p id="signatureMessage"></p>
                            <div class="mb-3">
                                <label>Password (or PIN)</label>
                                <input type="password" id="signaturePassword" class="form-control" required>
                            </div>
                            <div class="mb-3">
                                <label>Comment (optional)</label>
                                <textarea id="signatureComment" class="form-control" rows="2"></textarea>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary" id="signatureConfirmBtn">Sign</button>
                        </div>
                    </div>
                </div>
            </div>
        `);
    }
    $('#signatureMessage').text(options.title || 'Please enter your password to sign this record.');
    $('#signaturePassword').val('');
    $('#signatureComment').val('');
    var modal = new bootstrap.Modal(document.getElementById('signatureModal'));
    modal.show();
    $('#signatureConfirmBtn').off('click').on('click', function() {
        var password = $('#signaturePassword').val();
        var comment = $('#signatureComment').val();
        if (!password) {
            alert('Password is required');
            return;
        }
        options.onConfirm({password: password, comment: comment, recordId: options.recordId, recordType: options.recordType});
        modal.hide();
    });
}