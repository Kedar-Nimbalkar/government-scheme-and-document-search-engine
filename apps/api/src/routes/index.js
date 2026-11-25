import { Router } from 'express';
import { PUBLIC_ENDPOINTS as endpoints } from '../../../../packages/contracts/v2/http.js';
import { authenticationMiddleware } from '../middleware/auth.js';
import { authRateLimit, validate } from '../middleware/request.js';
import { uploadMiddleware } from '../middleware/upload.js';
import { credentials } from '../validators/auth.js';
import { autocompletePrefix, searchParameters } from '../validators/search.js';

export async function createRoutes(config, auth, phaseOne, handlers) {
  const router = Router();
  const { authentication, roles } = authenticationMiddleware(auth);
  const signedIn = authentication();
  const staff = roles('admin', 'department');
  const admin = roles('admin');
  const authLimit = authRateLimit(config);
  router.get(endpoints.health.path, phaseOne.health);
  router.post(endpoints.register.path, authLimit, validate('body', (body) => credentials(body, [], true)), phaseOne.register);
  router.post(endpoints.login.path, authLimit, validate('body', credentials), phaseOne.login);
  router.get(endpoints.me.path, signedIn, phaseOne.me);
  router.post(endpoints.logout.path, signedIn, phaseOne.logout);
  router.get(endpoints.search.path, validate('query', searchParameters), phaseOne.search);
  router.get(endpoints.autocomplete.path, validate('query', autocompletePrefix), phaseOne.autocomplete);
  router.get(endpoints.filters.path, phaseOne.filters);
  router.get(endpoints.bookmarks.path, signedIn, handlers.bookmarks);
  router.post(endpoints.createBookmark.path, signedIn, handlers.bookmark);
  router.delete(endpoints.deleteBookmark.path, signedIn, handlers.removeBookmark);
  router.get(endpoints.document.path, handlers.document);
  router.get(endpoints.pdf.path, handlers.pdf);
  router.get(endpoints.status.path, authentication(false), handlers.status);
  router.post(endpoints.upload.path, signedIn, staff, await uploadMiddleware(config), handlers.upload);
  router.get(endpoints.uploads.path, signedIn, staff, handlers.documents);
  router.get(endpoints.uploadSchemes.path, signedIn, staff, handlers.schemes);
  router.get(endpoints.adminUsers.path, signedIn, admin, handlers.accounts);
  router.post(endpoints.provisionDepartment.path, signedIn, admin, handlers.provision);
  return router;
}
